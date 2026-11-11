"""Offline regressions for managed installation, role safety and provider isolation."""

import importlib.util
import copy
import json
from datetime import datetime, timezone
import sys
import types
import unittest
from unittest.mock import Mock, patch

from helm import ROOT, render, resource
from test_mcp_credentials import native_values, studio_values


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
            "studio_managed": True,
            "oauth": {"authorization_url": "https://oauth.example.com/authorize", "pkce": True}}


def projection(spec):
    return {"id": spec["id"], "name": "Example", "authentication_model": spec["authentication_model"],
            "server_id": spec["server_id"], "gateway_id": "a" * 32, "approved_tools": spec["approved_tools"],
            "checks": [], "oauth_authorization_origin": "https://oauth.example.com"}


class SetupPublicationTests(unittest.TestCase):
    def run_main(self, specs, entries, reconcile_one, previous=None, source_changed=False, legacy_grants=()):
        """Exercise the installation pipeline without transport or credential access."""
        config = {"origin": "https://native.example.com", "nativeBudgetSeconds": 120,
                  "catalogHash": "a" * 64, "adminDiscovery": {"enabled": True},
                  "studioSetup": True, "serviceAccountEmail": "studio@example.com"}
        source = {"metadata": {"resourceVersion": "current-source"}, "data": {
            "catalogHash": config["catalogHash"], "registrations.json": json.dumps(specs),
            "studio.json": json.dumps(entries), "studioSetup": "true"}}
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
        discovery = {"admin_discovery_role_id": "discovery", "admin_discovery_ready": "true"}
        started = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        with patch.object(setup, "Path") as path, patch.object(setup, "API", side_effect=[Mock(), kube, Mock()]), \
                patch.object(setup, "Database") as database, patch.object(setup, "datetime") as clock, \
                patch.object(setup, "accounts") as accounts, \
                patch.object(setup, "admin_discovery_role", return_value=discovery), \
                patch.object(registrations, "reconcile_one", side_effect=reconcile_one), \
                patch.dict(setup.os.environ, {"PLATFORM_ADMIN_EMAIL": "admin@example.com"}):
            database.return_value.rows.return_value = list(legacy_grants)
            path.return_value.read_text.return_value = json.dumps(config)
            path.return_value.__truediv__.return_value.read_text.return_value = "synthetic-token"
            clock.now.return_value = started

            def verify_accounts(*args):
                clock.now.assert_called_once_with(timezone.utc)
                # A Refresh can complete later while setup is still running.
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
        spec = definition()
        unavailable = definition("unavailable") | {"server_id": "d" * 32}

        def verified(api, item, *args, **kwargs):
            if item["id"] == unavailable["id"]:
                raise registrations.SetupError("Native verification unavailable")
            return {"id": item["id"], "gateway_id": "a" * 32, "server_id": item["server_id"],
                    "state": "pending-discovery", "error_code": None,
                    "approved_config_hash": registrations.config_hash(item), "setup_mode": "studio-v1"}

        previous = {"setup_mode": "studio-v1", "studio.json": json.dumps([projection(item) | {"tool_names": {"read": "old_read"}}
                                              for item in (spec, unavailable)]),
                    "mappings.json": json.dumps([{"id": unavailable["id"], "server_id": unavailable["server_id"],
                        "gateway_id": "e" * 32, "approved_config_hash": registrations.config_hash(unavailable)}]),
                    "publication.json": "old-publication"}
        kube, _, error = self.run_main([spec, unavailable], [projection(spec), projection(unavailable)], verified, previous)
        self.assertIsNone(error)
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
        spec = definition() | {"oauth": None, "authentication_model": "shared-authentication"}
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
                self.assertEqual(body["team_id"], team)
                self.assertEqual(row["team_id"], body["team_id"])
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
        # Studio may publish newly discovered tools beyond the chart's check list.
        tools.append({"id": "c" * 32, "originalName": "newly_discovered", "enabled": True,
                      "gatewayId": gateways[0]["id"], "teamId": team, "ownerEmail": owner,
                      "visibility": "public", "integrationType": "MCP", "url": spec["upstream_url"]})
        servers[0]["associatedToolIds"] = [tools[0]["id"]]
        for enabled in (True, False):
            servers[0]["enabled"] = enabled
            saved = copy.deepcopy((gateways, servers, tools))
            mutations.clear()
            results = list(registrations.reconcile(api, [spec], team, owner, {spec["id"]: result}, (service_api, service)))
            self.assertEqual(results[0], result)
            self.assertEqual((gateways, servers, tools), saved)
            self.assertEqual(mutations, [])
        # Neither a foreign server nor an old gateway is silently adopted/reset.
        for row, field, value in ((servers[0], "ownerEmail", owner),
                                  (gateways[0], "description", marker.removesuffix("/studio-v1")),
                                  (tools[0], "teamId", "foreign")):
            before = row[field]
            row[field] = value
            with self.assertRaises(registrations.SetupError):
                registrations.reconcile_one(api, spec, team, owner, studio=(service_api, service))
            row[field] = before
        self.assertEqual(mutations, [])

    def test_changed_source_does_not_publish_new_hash_or_relabel_previous_snapshot(self):
        spec = definition()
        previous = {"setup_mode": "studio-v1", "studio.json": json.dumps([projection(spec) | {"tool_names": {}}]),
                    "mappings.json": "[]", "catalog_hash": "c" * 64}

        def verified(*args, **kwargs):
            return {"id": spec["id"], "gateway_id": "a" * 32, "server_id": spec["server_id"],
                    "state": "pending-discovery", "error_code": None,
                    "approved_config_hash": registrations.config_hash(spec)}

        kube, output, error = self.run_main([spec], [projection(spec)], verified, previous, source_changed=True)
        self.assertIsInstance(error, registrations.SetupError)
        self.assertIn("Catalog changed", str(error))
        self.assertFalse(any(call.args[0] == "PUT" for call in kube.request.call_args_list))
        self.assertEqual(output["data"], previous)

    def test_unresolved_legacy_grants_never_mutate_or_publish(self):
        for previous, grants in (({"operator_role_id": "legacy"}, []), ({}, [{"role_id": "legacy"}])):
            reconcile_one = Mock()
            kube, output, error = self.run_main([], [], reconcile_one, previous, legacy_grants=grants)
            self.assertIsInstance(error, registrations.SetupError)
            self.assertIn("access retirement", str(error))
            reconcile_one.assert_not_called()
            self.assertFalse(any(call.args[0] == "PUT" for call in kube.request.call_args_list))
            self.assertEqual(output["data"], previous)

    def test_chart_owned_installation_is_not_automatically_adopted(self):
        previous = {"studio.json": json.dumps([projection(definition())]), "ready": "true"}
        reconcile_one = Mock()
        kube, output, error = self.run_main([], [], reconcile_one, previous)
        self.assertIsInstance(error, registrations.SetupError)
        self.assertIn("reviewed in-place migration", str(error))
        reconcile_one.assert_not_called()
        self.assertFalse(any(call.args[0] == "PUT" for call in kube.request.call_args_list))
        self.assertEqual(output["data"], previous)

    def test_fresh_oauth_registration_preserves_exact_realm_issuer_without_updates(self):
        issuer = "https://identity.example.com/realms/Example/"
        spec = definition()
        spec["oauth"].update(issuer=issuer, token_url="https://identity.example.com/realms/Example/protocol/openid-connect/token",
                             client_id="example-app", redirect_uri="https://studio.example.com/oauth/callback",
                             scopes=["openid"], client_secret_ref={"name": "contextforge-oauth-apps", "key": spec["id"]})
        expected_oauth = {"authorization_url": "https://oauth.example.com/authorize", "issuer": issuer,
                          "token_url": "https://identity.example.com/realms/Example/protocol/openid-connect/token",
                          "client_id": "example-app", "redirect_uri": "https://studio.example.com/oauth/callback",
                          "scopes": ["openid"], "grant_type": "authorization_code"}
        gateways, servers, writes = [], [], []

        def request(actor, method, path, body=None):
            if method == "POST":
                writes.append((path, copy.deepcopy(body)))
                common = {"teamId": "team", "ownerEmail": actor, "createdBy": actor,
                          "visibility": "public", "enabled": True}
                if path == "/gateways":
                    self.assertEqual(actor, "admin@example.com")
                    # Target GatewayRead masks secrets, including on reruns.
                    gateways.append(common | {"id": "a" * 32, "name": body["name"], "description": body["description"],
                        "url": body["url"], "transport": body["transport"], "status": "active", "gatewayMode": "cache",
                        "authType": "oauth", "oauthConfig": expected_oauth | {"client_secret": "*****"}})
                elif path == "/servers":
                    self.assertEqual(actor, "studio@example.com")
                    server = body["server"]
                    servers.append(common | {"id": server["id"], "name": server["name"], "description": server["description"],
                        "oauthEnabled": False, "associatedToolIds": [], "associatedResources": [],
                        "associatedPrompts": [], "associatedA2aAgents": []})
                else:
                    self.fail("Unexpected native creation")
            elif method == "GET":
                if path.startswith("/gateways?"):
                    return gateways
                if path.startswith("/servers?"):
                    return servers
                if path == "/servers/" + spec["server_id"]:
                    return servers[0]
                return []
            else:
                self.fail("Native registration must not update or refresh")

        api = Mock(request=lambda *args: request("admin@example.com", *args))
        service_api = Mock(request=lambda *args: request("studio@example.com", *args))
        studio = (service_api, "studio@example.com")
        with patch.object(registrations.Path, "read_text", return_value="synthetic-app-secret"):
            result = registrations.reconcile_one(api, spec, "team", "admin@example.com", studio=studio)
        self.assertEqual(writes[0][0], "/gateways")
        self.assertEqual(writes[0][1]["oauth_config"], expected_oauth | {"client_secret": "synthetic-app-secret"})
        self.assertEqual(result["gateway_id"], "a" * 32)
        self.assertEqual(result["server_id"], spec["server_id"])
        self.assertEqual(result["state"], "pending-discovery")
        self.assertEqual(len(writes), 2)
        self.assertEqual(registrations.reconcile_one(api, spec, "team", "admin@example.com", studio=studio), result)
        self.assertEqual(len(writes), 2)
        for conflicting_issuer in (None, "", "https://identity.example.com/realms/other"):
            gateways[0]["oauthConfig"]["issuer"] = conflicting_issuer
            with self.assertRaises(registrations.SetupError):
                registrations.reconcile_one(api, spec, "team", "admin@example.com", studio=studio)
            self.assertEqual(len(writes), 2)

    def test_twenty_providers_isolate_failures_and_redact_errors(self):
        specs = [dict(definition(f"provider-{i}"), server_id=f"{i:032x}") for i in range(20)]

        def reconcile_one(api, spec, team, owner, **kwargs):
            if spec["id"] == "provider-3":
                raise RuntimeError("sensitive provider response")
            return {"id": spec["id"], "state": "pending-discovery", "error_code": None}

        with patch.object(registrations, "reconcile_one", side_effect=reconcile_one):
            results = list(registrations.reconcile(Mock(), specs, "team", "admin@example.com", studio=(Mock(), "studio@example.com")))
        self.assertEqual(sum(result["state"] == "pending-discovery" for result in results), 19)
        self.assertEqual(results[3]["error_code"], "provider-unavailable")
        self.assertNotIn("sensitive", json.dumps(results))


class RoleSafetyTests(unittest.TestCase):
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
        config = {"adminDiscovery": {"enabled": True}}
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


class RerunTests(unittest.TestCase):
    def test_selected_removed_modes_are_rejected_but_disabled_leftovers_are_accepted(self):
        for chart, values, config in (("contextforge", native_values(), ("contextforge", "setup")),
                                      ("studio/api", studio_values(), ("frontendStudio", "api", "contextforge"))):
            selected = values
            for key in config:
                selected = selected[key]
            selected["operatorDiscovery"] = {"enabled": False, "operatorEmail": "", "operatorSubject": ""}
            selected["publishOAuthTools"] = []
            render(chart, values)
            selected["operatorDiscovery"]["enabled"] = True
            output = render(chart, values, check=False)
            self.assertNotEqual(output.returncode, 0)
            self.assertIn("operatorDiscovery has been removed", output.stderr)
            selected["operatorDiscovery"]["enabled"] = False
            values["mcp-servers"]["studioSetup"]["enabled"] = False
            output = render(chart, values, check=False)
            self.assertNotEqual(output.returncode, 0)
            self.assertIn("chart-owned", output.stderr)
        values = native_values()
        values["contextforge"]["setup"]["publishOAuthTools"] = ["example"]
        output = render("contextforge", values, check=False)
        self.assertNotEqual(output.returncode, 0)
        self.assertIn("publishOAuthTools has been removed", output.stderr)
        # Retired selections must not disappear silently behind disabled setup.
        values["contextforge"]["setup"]["enabled"] = False
        output = render("contextforge", values, check=False)
        self.assertNotEqual(output.returncode, 0)
        self.assertIn("publishOAuthTools has been removed", output.stderr)
        values["contextforge"]["setup"]["publishOAuthTools"] = []
        values["contextforge"]["setup"]["operatorDiscovery"] = {"enabled": True}
        output = render("contextforge", values, check=False)
        self.assertNotEqual(output.returncode, 0)
        self.assertIn("operatorDiscovery has been removed", output.stderr)
        values["contextforge"]["setup"]["operatorDiscovery"]["enabled"] = False
        values["mcp-servers"]["studioSetup"]["enabled"] = False
        render("contextforge", values)
        output = render("agentgateway", {"mcp-servers": {"catalog": {
            "presets": {"context7": {"enabled": True}}}}}, check=False)
        self.assertNotEqual(output.returncode, 0)
        self.assertIn("chart-owned publication has been removed", output.stderr)

    def test_rerun_selects_only_setup_and_removes_hook_metadata(self):
        output = render("contextforge", native_values(), release="contextforge", namespace="contextforge")
        original = resource(output, "Job", "contextforge-setup")
        job = rerun.rerun_job([resource(output, "Job", "contextforge-migration"), original],
                              "contextforge", "contextforge", "contextforge-setup-rerun")
        self.assertNotIn("annotations", job["metadata"])
        self.assertEqual(job["spec"], original["spec"])
        with self.assertRaises(ValueError):
            rerun.rerun_job([original], "contextforge", "contextforge", "contextforge-setup")


if __name__ == "__main__":
    unittest.main()
