"""Offline regressions for operator grants, exact publication and provider isolation."""

import importlib.util
import json
import sys
import types
import unittest
from unittest.mock import Mock, patch

from helm import ROOT, render, resource


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registrations = load("registrations", ROOT / "charts/contextforge/files/registrations.py")
# Transport dependencies ship in the pinned native image, not Base validation.
with patch.dict(sys.modules, {"registrations": registrations, "httpx": types.ModuleType("httpx"),
                             "psycopg": types.ModuleType("psycopg"), "psycopg.rows": Mock(dict_row=None)}):
    setup = load("native_setup", ROOT / "charts/contextforge/files/setup.py")
rerun = load("rerun", ROOT / "scripts/contextforge_setup_job.py")


def definition(identity="example"):
    return {"id": identity, "provider": "example", "authentication_model": "individual-authentication",
            "server_id": "b" * 32, "upstream_url": "https://mcp.example.com/" + identity,
            "transport": "STREAMABLEHTTP", "visibility": "public", "approved_tools": ["read"],
            "oauth": {"authorization_url": "https://oauth.example.com/authorize"}}


def projection(spec):
    return {"id": spec["id"], "name": "Example", "authentication_model": spec["authentication_model"],
            "server_id": spec["server_id"], "gateway_id": "a" * 32, "approved_tools": spec["approved_tools"],
            "checks": [], "oauth_authorization_origin": "https://oauth.example.com"}


class SetupPublicationTests(unittest.TestCase):
    def test_oauth_pending_server_and_exact_publication_never_refresh(self):
        spec = definition()
        gateway = {"id": "a" * 32, "name": "neurwerk-contextforge-example", "url": spec["upstream_url"]}
        server = {"id": spec["server_id"], "name": gateway["name"]}
        tool = {"id": "c" * 32, "originalName": "read", "enabled": True, "gatewayId": gateway["id"],
                "teamId": "team", "ownerEmail": "admin@example.com", "visibility": "public",
                "integrationType": "MCP", "url": spec["upstream_url"]}
        for tools in ([], [tool]):
            calls = []

            def request(method, path, body=None):
                calls.append((method, path, body))
                self.assertNotIn("refresh", path)
                return tools if path.startswith("/tools?") else server

            api = Mock(request=request)
            with patch.object(registrations, "catalog", side_effect=lambda api, kind: [gateway] if kind == "gateways" else [server]), \
                    patch.object(registrations, "check_gateway"), \
                    patch.object(registrations, "members", side_effect=[set(), {tool["id"]} if tools else set()]):
                result = registrations.reconcile_one(api, spec, "team", "admin@example.com")
            self.assertEqual(result["state"], "published" if tools else "pending-discovery")
            mutations = [call for call in calls if call[0] != "GET"]
            self.assertEqual(mutations, [("PUT", "/servers/" + spec["server_id"], {"associated_tools": [tool["id"]]})] if tools else [])

    def test_tool_conflict_precedes_server_mutation(self):
        spec = definition()
        gateway = {"id": "a" * 32, "name": "neurwerk-contextforge-example", "url": spec["upstream_url"]}
        api = Mock()
        with patch.object(registrations, "catalog", side_effect=[[gateway], [{"id": spec["server_id"]}]]), \
                patch.object(registrations, "check_gateway"), \
                patch.object(registrations, "members", return_value={"foreign"}), \
                patch.object(registrations, "approved", return_value={"approved"}):
            with self.assertRaises(registrations.SetupError):
                registrations.reconcile_one(api, spec, "team", "admin@example.com")
        api.request.assert_not_called()

    def test_twenty_providers_isolate_failures_and_redact_errors(self):
        specs = [dict(definition(f"provider-{i}"), server_id=f"{i:032x}") for i in range(20)]
        def reconcile_one(api, spec, team, owner):
            if spec["id"] == "provider-3":
                raise RuntimeError("sensitive provider response")
            return {"id": spec["id"], "state": "published", "error_code": None}
        with patch.object(registrations, "reconcile_one", side_effect=reconcile_one):
            results = registrations.reconcile(Mock(), specs, "team", "admin@example.com")
        self.assertEqual(sum(result["state"] == "published" for result in results), 19)
        self.assertEqual(results[3]["error_code"], "provider-unavailable")
        self.assertNotIn("sensitive", json.dumps(results))

    def test_failed_publication_preserves_only_matching_approved_configuration(self):
        spec = definition()
        entry = projection(spec) | {"tool_names": {"read": "example_read"}}
        mapping = {"id": spec["id"], "gateway_id": entry["gateway_id"], "server_id": spec["server_id"],
                   "approved_config_hash": registrations.config_hash(spec), "state": "published", "error_code": None}
        previous = {"studio.json": json.dumps([entry]), "mappings.json": json.dumps([mapping])}
        error = {"id": spec["id"], "state": "error", "error_code": "provider-unavailable"}
        entries, _, statuses = setup.project(Mock(), [spec], [projection(spec)], [error], previous)
        self.assertEqual(entries, [entry])
        self.assertEqual(statuses, [error])
        for changed_spec, changed_entry in ((spec | {"approved_tools": ["other"]}, projection(spec)),
                                            (spec, projection(spec) | {"checks": [{"name": "new"}]}),
                                            (spec | {"gateway_id": "d" * 32}, projection(spec))):
            entries, mappings, _ = setup.project(Mock(), [changed_spec], [changed_entry], [error], previous)
            self.assertEqual((entries, mappings), ([], []))

    def test_partial_verification_failure_never_publishes_new_tool_mapping(self):
        spec = definition()
        result = {"id": spec["id"], "gateway_id": "a" * 32, "server_id": spec["server_id"],
                  "state": "published", "error_code": None}
        api = Mock()
        api.request.return_value = [{"originalName": "unapproved", "name": "unapproved"}]
        entries, mappings, statuses = setup.project(api, [spec], [projection(spec)], [result], {})
        self.assertEqual((entries, mappings), ([], []))
        self.assertEqual(statuses[0]["error_code"], "verification-failed")


class OperatorGrantTests(unittest.TestCase):
    def test_first_grant_is_only_named_team_discovery_and_is_confirmed(self):
        api, db = Mock(), Mock()
        config = {"serviceAccountEmail": "studio@example.com", "operatorDiscovery": {
            "enabled": True, "operatorEmail": "operator@example.com", "operatorSubject": "verified-subject",
            "roleName": "discovery"}}
        ids = {"team_id": "team", "global_role_id": "global", "team_role_id": "invoke"}
        baseline = [{"role_id": "global", "scope": "global", "scope_id": None, "is_active": True, "expires_at": None},
                    {"role_id": "invoke", "scope": "team", "scope_id": "team", "is_active": True, "expires_at": None}]
        db.rows.side_effect = [[{"team_id": "team", "role": "member", "is_active": True}], [], [],
                               baseline, baseline + [baseline[1] | {"role_id": "discovery"}]]
        api.request.return_value = {"email": "operator@example.com", "is_admin": False,
                                    "is_active": True, "email_verified": True}
        with patch.object(setup, "role", return_value="discovery"):
            result = setup.operator_account(api, db, config, "admin@example.com", ids, {})
        self.assertEqual(result, {"operator_email": "operator@example.com", "operator_subject": "verified-subject",
                                  "operator_role_id": "discovery"})
        api.request.assert_called_with("POST", "/rbac/users/operator%40example.com/roles",
                                       {"role_id": "discovery", "scope": "team", "scope_id": "team"})

    def test_dormant_missing_and_extra_operator_grants_are_never_repaired(self):
        config = {"serviceAccountEmail": "studio@example.com", "operatorDiscovery": {
            "enabled": True, "operatorEmail": "operator@example.com", "operatorSubject": "verified-subject",
            "roleName": "discovery"}}
        ids = {"team_id": "team", "global_role_id": "global", "team_role_id": "invoke"}
        baseline = [{"role_id": "global", "scope": "global", "scope_id": None, "is_active": True, "expires_at": None},
                    {"role_id": "invoke", "scope": "team", "scope_id": "team", "is_active": True, "expires_at": None}]
        for grants in (baseline, baseline + [baseline[1] | {"role_id": "discovery", "is_active": False}],
                       baseline + [baseline[1] | {"role_id": "administrator"}]):
            api, db = Mock(), Mock()
            api.request.return_value = {"email": "operator@example.com", "is_admin": False,
                                        "is_active": True, "email_verified": True}
            db.rows.side_effect = [[{"team_id": "team", "role": "member", "is_active": True}],
                                   [{"id": "discovery"}], [], grants]
            with patch.object(setup, "role", return_value="discovery") as role:
                with self.assertRaises(registrations.SetupError):
                    setup.operator_account(api, db, config, "admin@example.com", ids, {})
                self.assertEqual(role.call_args.args[3:5], ("team", ["gateways.update"]))
            self.assertTrue(all(call.args[0] == "GET" for call in api.request.call_args_list))

    def test_disabled_operator_does_not_provision_or_read_native_identity(self):
        api, db = Mock(), Mock()
        self.assertEqual(setup.operator_account(api, db, {"operatorDiscovery": {"enabled": False}}, "admin", {}, {}), {})
        api.request.assert_not_called()
        db.rows.assert_not_called()


class RerunTests(unittest.TestCase):
    def test_operator_admission_rejects_missing_named_subject_in_both_charts(self):
        operator = {"enabled": True, "operatorEmail": "operator@example.com", "operatorSubject": ""}
        studio_values = {"frontendStudio": {"api": {"contextforge": {
            "enabled": True, "accountOnboardingEnabled": True, "connectionsEnabled": True,
            "setupConfigMapName": "contextforge-setup", "operatorDiscovery": operator}}}}
        native_values = {"contextforge": {"trustedProxy": {
            "enabled": True, "studioOrigin": "https://studio.example.com",
            "defaultUserRole": "reader", "defaultTeamMemberRole": "member"}, "setup": {
            "enabled": True, "serviceAccountEmail": "studio@example.com", "operatorDiscovery": operator,
            "kubernetesApiEgress": [{"cidr": "192.0.2.1/32", "port": 443}]}}}
        for chart, values in (("contextforge", native_values), ("studio/api", studio_values)):
            output = render(chart, values, check=False)
            self.assertNotEqual(output.returncode, 0)
            self.assertIn("operatorSubject", output.stderr)

    def test_rerun_selects_only_setup_and_removes_hook_metadata(self):
        values = {"contextforge": {"trustedProxy": {"enabled": True, "studioOrigin": "https://studio.example.com",
                    "defaultUserRole": "reader", "defaultTeamMemberRole": "member"},
                    "setup": {"enabled": True, "serviceAccountEmail": "studio@example.com",
                              "kubernetesApiEgress": [{"cidr": "192.0.2.1/32", "port": 443}]}}}
        output = render("contextforge", values, release="contextforge", namespace="contextforge")
        original = resource(output, "Job", "contextforge-setup")
        job = rerun.rerun_job([resource(output, "Job", "contextforge-migration"), original],
                              "contextforge", "contextforge", "contextforge-setup-rerun")
        self.assertNotIn("annotations", job["metadata"])
        self.assertEqual(job["spec"], original["spec"])
        with self.assertRaises(ValueError):
            rerun.rerun_job([original], "contextforge", "contextforge", "contextforge-setup")


if __name__ == "__main__":
    unittest.main()
