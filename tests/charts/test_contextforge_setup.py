"""Offline regressions for operator grants, exact publication and provider isolation."""

import importlib.util
import copy
import json
from datetime import datetime, timezone
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
    def run_main(self, specs, entries, reconcile_one, api, previous=None, source_changed=False, managed=False):
        """Exercise the publication pipeline without transport or credential access."""
        config = {"origin": "https://native.example.com", "nativeBudgetSeconds": 120,
                  "catalogHash": "a" * 64, "operatorDiscovery": {"enabled": False},
                  "studioSetup": managed, "serviceAccountEmail": "studio@example.com"}
        source = {"metadata": {"resourceVersion": "current-source"}, "data": {
            "catalogHash": config["catalogHash"], "registrations.json": json.dumps(specs),
            "studio.json": json.dumps(entries), "studioSetup": str(managed).lower()}}
        output = {"metadata": {"labels": {"app.kubernetes.io/part-of": "contextforge"}},
                  "data": copy.deepcopy(previous or {})}
        kube = Mock()
        source_reads = 0
        def request(method, path, body=None):
            nonlocal source_reads
            if method == "PUT":
                return body
            if path.endswith("infra-agentgateway-mcp-catalog"):
                source_reads += 1
                result = copy.deepcopy(source)
                if source_changed and source_reads > 1:
                    result["data"]["catalogHash"] = "d" * 64
                return result
            return copy.deepcopy(output)
        kube.request.side_effect = request
        ids = {"team_id": "team", "global_role_id": "global", "team_role_id": "invoke"}
        discovery = {"admin_discovery_role_id": "discovery", "admin_discovery_ready": "true"} if managed else {}
        started = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        with patch.object(setup, "Path") as path, patch.object(setup, "API", side_effect=[api, kube, Mock()]), \
                patch.object(setup, "Database"), patch.object(setup, "datetime") as clock, \
                patch.object(setup, "accounts") as accounts, \
                patch.object(setup, "admin_discovery_role", return_value=discovery), \
                patch.object(registrations, "reconcile_one", side_effect=reconcile_one), \
                patch.dict(setup.os.environ, {"PLATFORM_ADMIN_EMAIL": "admin@example.com"}):
            path.return_value.read_text.return_value = json.dumps(config)
            path.return_value.__truediv__.return_value.read_text.return_value = "synthetic-token"
            clock.now.return_value = started
            def verify_accounts(*args):
                # Verification-start time must already have been captured.
                clock.now.assert_called_once_with(timezone.utc)
                # A Discover can complete later while setup is still running.
                clock.now.return_value = datetime(2026, 10, 8, 12, 1, tzinfo=timezone.utc)
                return ids
            accounts.side_effect = verify_accounts
            try:
                setup.main()
            except registrations.SetupError as exc:
                return kube, output, exc
            clock.now.assert_called_once_with(timezone.utc)
        return kube, output, None

    def test_studio_installation_snapshot_matches_loader_without_importing_publication(self):
        spec = definition() | {"studio_managed": True}
        unavailable = definition("unavailable") | {"studio_managed": True, "server_id": "d" * 32}
        def verified(api, item, *args, **kwargs):
            if item["id"] == unavailable["id"]:
                raise registrations.SetupError("Native verification unavailable")
            return {"id": item["id"], "gateway_id": "a" * 32, "server_id": item["server_id"],
                    "state": "pending-discovery", "error_code": None,
                    "approved_config_hash": registrations.config_hash(item), "setup_mode": "studio-v1"}
        previous = {"studio.json": json.dumps([projection(item) | {"tool_names": {"read": "old_read"}}
                                              for item in (spec, unavailable)]),
                    "mappings.json": json.dumps([{"id": unavailable["id"], "server_id": unavailable["server_id"],
                        "gateway_id": "e" * 32, "approved_config_hash": registrations.config_hash(unavailable)}]),
                    "publication.json": "old-publication"}
        with patch.object(setup, "project") as publish, patch.object(setup, "legacy_publication_guard") as guard:
            kube, _, error = self.run_main([spec, unavailable], [projection(spec), projection(unavailable)],
                                           verified, Mock(), previous, managed=True)
        self.assertIsNone(error)
        publish.assert_not_called()
        guard.assert_not_called()
        data = next(call.args[2]["data"] for call in kube.request.call_args_list if call.args[0] == "PUT")
        self.assertEqual(data["setup_mode"], "studio-v1")
        self.assertEqual({key: data[key] for key in ("team_id", "global_role_id", "team_role_id",
            "admin_discovery_role_id", "admin_discovery_ready", "ready")}, {
            "team_id": "team", "global_role_id": "global", "team_role_id": "invoke",
            "admin_discovery_role_id": "discovery", "admin_discovery_ready": "true", "ready": "true"})
        catalog = json.loads(data["studio.json"])
        self.assertEqual([entry["tool_names"] for entry in catalog], [{}, {}])
        self.assertEqual([entry["gateway_id"] for entry in catalog], ["a" * 32, ""])
        self.assertEqual([entry["id"] for entry in json.loads(data["mappings.json"])], [spec["id"]])
        self.assertEqual(data["catalog_hash"], "a" * 64)
        self.assertEqual(json.loads(data["publication.json"]), {
            "catalog_hash": data["catalog_hash"], "checked_at": "2026-10-08T12:00:00+00:00",
            "integrations": [{"id": spec["id"], "state": "pending-discovery", "error_code": None},
                             {"id": unavailable["id"], "state": "error", "error_code": "verification-failed"}]})
        self.assertEqual(sum(call.args[0] == "PUT" for call in kube.request.call_args_list), 1)

    def test_studio_initialization_is_empty_and_rerun_preserves_saved_native_state(self):
        spec = definition() | {"studio_managed": True, "oauth": None, "authentication_model": "shared-authentication"}
        owner, service, team = "admin@example.com", "studio@example.com", "team"
        marker = "neurwerk-contextforge/example/example/shared-authentication/studio-v1"
        gateways, servers, tools, mutations = [], [], [], []

        def request(actor, method, path, body=None):
            if method != "GET":
                mutations.append((actor, method, path, body))
            if method == "POST" and path == "/gateways":
                self.assertEqual(actor, owner)
                gateways.append({"id": "a" * 32, "name": body["name"], "description": body["description"],
                    "url": body["url"], "transport": body["transport"], "teamId": team,
                    "ownerEmail": owner, "createdBy": owner, "visibility": "public",
                    "enabled": True, "status": "pending", "gatewayMode": "cache", "reachable": False})
                return gateways[0]
            if method == "POST" and path == "/servers":
                self.assertEqual(actor, service)
                row = body["server"]
                servers.append({"id": row["id"], "name": row["name"], "description": row["description"],
                    "teamId": team, "ownerEmail": service, "createdBy": service, "visibility": "public",
                    "enabled": True, "oauthEnabled": False, "associatedResources": [], "associatedPrompts": [],
                    "associatedA2aAgents": [], "associatedToolIds": row["associated_tools"]})
                return servers[0]
            self.assertEqual(method, "GET", "Bootstrap must never reset or republish existing native servers")
            if path.startswith("/gateways?"):
                return gateways
            if path.startswith("/servers?"):
                return servers
            if "/tools?" in path:
                return tools
            if "/resources?" in path or "/prompts?" in path:
                return []
            if path == "/servers/" + spec["server_id"]:
                return servers[0]
            self.fail("Unexpected native setup request")

        api = Mock(request=lambda *args: request(owner, *args))
        service_api = Mock(request=lambda *args: request(service, *args))
        result = registrations.reconcile_one(api, spec, team, owner, studio=(service_api, service))
        self.assertEqual([call[2] for call in mutations], ["/gateways", "/servers"])
        self.assertEqual(servers[0]["associatedToolIds"], [])
        self.assertEqual(servers[0]["description"], marker + "/" + gateways[0]["id"])
        # A later Studio choice may include newly discovered tools beyond the old
        # static approved list, or disable the server. Bootstrap must preserve it.
        tools.append({"id": "c" * 32, "originalName": "newly_discovered", "enabled": True,
                      "gatewayId": gateways[0]["id"], "teamId": team, "ownerEmail": owner,
                      "visibility": "public", "integrationType": "MCP", "url": spec["upstream_url"]})
        servers[0]["associatedToolIds"] = [tools[0]["id"]]
        servers[0]["enabled"] = False
        saved = copy.deepcopy(servers)
        mutations.clear()
        rerun = list(registrations.reconcile(api, [spec], team, owner, {spec["id"]: result}, (service_api, service)))
        self.assertNotEqual(rerun[0]["state"], "error")
        self.assertEqual(servers, saved)
        self.assertEqual(mutations, [])
        # Neither a foreign server nor an old gateway is silently adopted/reset.
        for row, field, value in ((servers[0], "ownerEmail", owner), (gateways[0], "description", marker.removesuffix("/studio-v1"))):
            before = row[field]
            row[field] = value
            with self.assertRaises(registrations.SetupError):
                registrations.reconcile_one(api, spec, team, owner, studio=(service_api, service))
            row[field] = before
        self.assertEqual(mutations, [])

    def test_successful_first_provider_survives_later_budget_exhaustion_and_uses_start_time(self):
        first = definition("first")
        second = definition("late") | {"server_id": "d" * 32}
        exhausted = False
        api = Mock()
        def project_request(*args):
            if exhausted:
                raise registrations.SetupError("Native setup time budget exhausted")
            return [{"originalName": "read", "name": "read"}]
        api.request.side_effect = project_request
        def reconcile_one(api, spec, team, owner):
            nonlocal exhausted
            if spec["id"] == "late":
                exhausted = True
                raise registrations.SetupError("Native setup time budget exhausted")
            return {"id": spec["id"], "gateway_id": "a" * 32, "server_id": spec["server_id"],
                    "state": "published", "error_code": None,
                    "approved_config_hash": registrations.config_hash(spec)}
        kube, _, error = self.run_main([first, second], [projection(first), projection(second)], reconcile_one, api)
        self.assertIsNone(error)
        data = next(call.args[2]["data"] for call in kube.request.call_args_list if call.args[0] == "PUT")
        self.assertEqual(data["team_id"], "team")
        catalog = json.loads(data["studio.json"])
        self.assertEqual([entry["id"] for entry in catalog], ["first"])
        self.assertEqual(catalog[0]["tool_names"], {"read": "first_read"})
        publication = json.loads(data["publication.json"])
        self.assertEqual(data["catalog_hash"], "a" * 64)
        self.assertEqual(data["catalog_hash"], publication["catalog_hash"])
        self.assertEqual(publication["checked_at"], "2026-10-08T12:00:00+00:00")
        self.assertEqual([entry["state"] for entry in publication["integrations"]], ["published", "error"])

    def test_legacy_last_oauth_failure_never_overwrites_or_restarts_previous_consumer(self):
        spec = definition()
        old_entry = projection(spec) | {"tool_names": {}}
        previous = {"studio.json": json.dumps([old_entry]), "mappings.json": "[]", "catalog_hash": "c" * 64,
                    "publication.json": json.dumps({"catalog_hash": "c" * 64, "checked_at": "old-time"})}
        def fail(*args):
            raise registrations.SetupError("Native verification unavailable")
        kube, output, error = self.run_main([spec], [projection(spec)], fail, Mock(), previous)
        self.assertIsInstance(error, registrations.SetupError)
        self.assertIn("legacy Studio connections", str(error))
        self.assertFalse(any(call.args[0] == "PUT" for call in kube.request.call_args_list))
        self.assertEqual(output["data"], previous)
        # Verified real pending IDs are permitted; unknown/absent OAuth IDs are not invented.
        setup.legacy_publication_guard({"operatorDiscovery": {"enabled": False}}, [spec], [old_entry], previous)
        setup.legacy_publication_guard({"operatorDiscovery": {"enabled": True}}, [spec], [], previous)
        with self.assertRaises(registrations.SetupError):
            setup.legacy_publication_guard({"operatorDiscovery": {"enabled": False}}, [], [], previous)

    def test_changed_source_does_not_publish_new_hash_or_relabel_previous_snapshot(self):
        spec = definition()
        previous = {"studio.json": json.dumps([projection(spec) | {"tool_names": {}}]),
                    "mappings.json": "[]", "catalog_hash": "c" * 64}
        def verified(*args):
            return {"id": spec["id"], "gateway_id": "a" * 32, "server_id": spec["server_id"],
                    "state": "pending-discovery", "error_code": None,
                    "approved_config_hash": registrations.config_hash(spec)}
        kube, output, error = self.run_main([spec], [projection(spec)], verified, Mock(), previous,
                                            source_changed=True)
        self.assertIsInstance(error, registrations.SetupError)
        self.assertIn("Catalog changed", str(error))
        self.assertFalse(any(call.args[0] == "PUT" for call in kube.request.call_args_list))
        self.assertEqual(output["data"], previous)

    def test_verified_pending_registration_keeps_real_connect_ids_without_extra_native_call(self):
        spec = definition()
        result = {"id": spec["id"], "gateway_id": "a" * 32, "server_id": spec["server_id"],
                  "state": "pending-discovery", "error_code": None,
                  "approved_config_hash": registrations.config_hash(spec)}
        api = Mock()
        api.request.side_effect = registrations.SetupError("Native setup time budget exhausted")
        entries, _, statuses = setup.project(api, [spec], [projection(spec)], [result], {})
        self.assertEqual(entries[0]["gateway_id"], result["gateway_id"])
        self.assertEqual(entries[0]["server_id"], result["server_id"])
        self.assertEqual(entries[0]["tool_names"], {})
        self.assertEqual(statuses[0]["state"], "pending-discovery")
        api.request.assert_not_called()
        setup.legacy_publication_guard({"operatorDiscovery": {"enabled": False}}, [spec], entries, {})

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
            results = list(registrations.reconcile(Mock(), specs, "team", "admin@example.com"))
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
    def test_owned_role_reconciles_exact_permissions_after_ownership_check(self):
        desired = sorted(setup.PROVISION + setup.PUBLISH)
        row = {"id": "owner", "name": "provisioner", "scope": "team", "description": setup.MARKER,
               "created_by": "admin@example.com", "is_active": True, "inherits_from": None,
               "is_system_role": False, "permissions": setup.PROVISION + ["unwanted"]}
        db, api = Mock(), Mock()
        db.rows.return_value = [{"id": "owner"}]
        def request(method, path, body=None):
            if method == "PUT":
                row.update(body)
            return copy.deepcopy(row)
        api.request.side_effect = request
        self.assertEqual(setup.role(api, db, "provisioner", "team", desired, "admin@example.com"), "owner")
        self.assertEqual(row["permissions"], desired)
        api.request.assert_any_call("PUT", "/rbac/roles/owner", {"permissions": desired})
        row.update(created_by="other@example.com", permissions=[])
        api.reset_mock()
        with self.assertRaises(registrations.SetupError):
            setup.role(api, db, "provisioner", "team", desired, "admin@example.com")
        self.assertTrue(all(call.args[0] == "GET" for call in api.request.call_args_list))

    def test_admin_discovery_creates_only_scoped_role_without_a_named_account(self):
        api, db = Mock(), Mock()
        config = {"operatorDiscovery": {"enabled": False}, "adminDiscovery": {"enabled": True}}
        with patch.object(setup, "role", return_value="discovery") as role:
            result = setup.admin_discovery_role(api, db, config, "admin@example.com", {})
            self.assertEqual(result, {"admin_discovery_role_id": "discovery", "admin_discovery_ready": "true"})
            self.assertEqual(role.call_args.args[2:5], ("contextforge-tool-discovery", "team", ["gateways.update"]))
            self.assertEqual(role.call_args.args[-1], setup.MARKER + "/admin-discovery")
        api.request.assert_not_called()
        db.rows.assert_not_called()
        with self.assertRaises(registrations.SetupError):
            setup.admin_discovery_role(api, db, config, "admin@example.com", {"operator_role_id": "legacy"})
        self.assertEqual(setup.admin_discovery_role(api, db, {}, "admin@example.com", {}), {})

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
    def test_role_based_discovery_requires_no_binding_and_rejects_mixed_modes(self):
        native = {"contextforge": {"trustedProxy": {"enabled": True, "studioOrigin": "https://studio.example.com",
                    "defaultUserRole": "reader", "defaultTeamMemberRole": "member"},
                    "setup": {"enabled": True, "serviceAccountEmail": "studio@example.com",
                              "adminDiscovery": {"enabled": True},
                              "kubernetesApiEgress": [{"cidr": "192.0.2.1/32", "port": 443}]}}}
        studio = {"frontendStudio": {"api": {"contextforge": {
            "enabled": True, "accountOnboardingEnabled": True, "connectionsEnabled": True,
            "studioOrigin": "https://studio.example.com", "serviceAccountEmail": "studio@example.com",
            "catalogConfigMapName": "contextforge-setup", "setupConfigMapName": "contextforge-setup",
            "adminDiscovery": {"enabled": True}}}}}
        for chart, values, config in (("contextforge", native, native["contextforge"]["setup"]),
                                      ("studio/api", studio, studio["frontendStudio"]["api"]["contextforge"])):
            render(chart, values)
            config["operatorDiscovery"] = {"enabled": True}
            output = render(chart, values, check=False)
            self.assertNotEqual(output.returncode, 0)
            self.assertIn("not both", output.stderr)

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
