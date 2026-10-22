"""Grouped ownership, scoped discovery and bounded native policy growth."""

import copy
import json
import unittest

from celpy import Environment, json_to_cel

import test_agentgateway_inspection as inspection
from helm import documents, env_value, render, resource
from test_agentgateway_inspection import (
    PROVIDER_GROUPS,
    binding_values,
    catalog_owners,
)


def capacity_values(count, inspected=0):
    values = binding_values()
    values["self-hosted-api-providers"] = []
    values["external-api-providers"] = [{
        "id": f"provider-{i}", "enabled": True, "providerId": "openai",
        "upstreamURL": f"https://provider-{i}.example.test/v1",
        "credentialSources": ["caller_supplied"],
        "routes": {"api": {"enabled": True, "path": f"/provider-{i}/api"},
                   "inspected": {"enabled": i < inspected}},
        "profiles": ["openai-chat-completions-v1"],
        "models": [{"name": f"model/{i}", "upstreamModel": f"upstream-{i}"}],
    } for i in range(count)]
    values["authKeycloak"]["agentgatewayClientRoles"] = ["llm:invoke", *[
        f"model:model/{i}:invoke" for i in range(count)]]
    values["aiGateway"]["proxy"]["enabled"] = inspected > 0
    return values


def catalog(result):
    return json.loads(resource(result, "ConfigMap", "agentgateway-catalog-config-map")["data"]["catalog.json"])


class GroupedGatewayTests(unittest.TestCase):
    evaluate = inspection.ApprovedOidcTests.evaluate

    def test_studio_cors_is_exact_and_attached_to_every_external_route(self):
        values = capacity_values(65)
        values["frontendStudio"] = {"studio": {"hostname": "studio.example.test"}}
        rendered = render("agentgateway", values)
        middleware = resource(rendered, "Middleware", "infra-agentgateway-studio-cors")
        headers = middleware["spec"]["headers"]
        self.assertEqual(headers["accessControlAllowOriginList"], ["https://studio.example.test"])
        self.assertIs(headers["accessControlAllowCredentials"], False)
        self.assertEqual(headers["accessControlAllowMethods"], ["GET", "POST", "DELETE", "OPTIONS"])
        self.assertIn("X-Neurwerk-Authorization", headers["accessControlAllowHeaders"])
        self.assertIn("Mcp-Protocol-Version", headers["accessControlAllowHeaders"])
        self.assertNotIn("*", headers["accessControlAllowHeaders"])
        route = resource(rendered, "HTTPRoute", "infra-agentgateway-external-httproute")
        self.assertGreater(len(route["spec"]["rules"]), 1)
        for rule in route["spec"]["rules"]:
            self.assertEqual(rule["filters"], [{"type": "ExtensionRef", "extensionRef": {
                "group": "traefik.io", "kind": "Middleware", "name": "infra-agentgateway-studio-cors",
            }}])
            self.assertEqual(rule["backendRefs"][0]["name"], "infra-agentgateway-gateway")
        for hostname in ("place.holder", ""):
            values["frontendStudio"]["studio"]["hostname"] = hostname
            without_cors = render("agentgateway", values)
            self.assertFalse(any(doc["kind"] == "Middleware" and doc["metadata"]["name"] == "infra-agentgateway-studio-cors"
                                 for doc in documents(without_cors)))
            self.assertTrue(all("filters" not in rule for rule in resource(
                without_cors, "HTTPRoute", "infra-agentgateway-external-httproute"
            )["spec"]["rules"]))

    def test_legacy_inputs_are_rejected_even_when_empty(self):
        for key, value in [("bindings", []), ("localModelDiscovery", {})]:
            values = binding_values()
            values["aiGateway"][key] = value
            self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)
        self.assertNotEqual(render("agentgateway", {"mcp": {}}, check=False).returncode, 0)
        for key in ["api", "apiPath", "inspect", "local"]:
            values = binding_values()
            values["external-api-providers"][0][key] = ""
            self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)

    def test_ownership_and_references_are_unambiguous(self):
        values = binding_values()
        original = values["external-api-providers"][0]
        for override in [
            {"id": original["id"]},
            {"upstreamURL": original["upstreamURL"]},
            {"upstreamURL": "https://provider.example.test:443/v1/"},
            {"models": original["models"]},
            {"routes": {
                "api": {"enabled": True, "path": "/overlap/nested"}, "inspected": {"enabled": False}}},
        ]:
            candidate = copy.deepcopy(values)
            candidate["external-api-providers"][0]["routes"]["api"]["path"] = "/overlap"
            duplicate = copy.deepcopy(original)
            duplicate.update(id="another-owner", upstreamURL="https://other.example.test/v1",
                             routes={"api": {"enabled": True, "path": "/other/api"}, "inspected": {"enabled": False}},
                             models=[{"name": "other-chat", "upstreamModel": "other-upstream"}])
            candidate["authKeycloak"]["agentgatewayClientRoles"].append("model:other-chat:invoke")
            duplicate.update(override)
            candidate["external-api-providers"].append(duplicate)
            self.assertNotEqual(render("agentgateway", candidate, check=False).returncode, 0)
        result = catalog(render("agentgateway", values))
        self.assertEqual(set(result), {"schema_version", "catalog_revision", "policy_revision", "profiles",
                                      *PROVIDER_GROUPS, "mcp-servers"})
        all_ids = set()
        destinations = set()
        routes = {}
        profiles = {profile["id"] for profile in result["profiles"]}
        for owner in catalog_owners(result):
            self.assertEqual(set(owner), {"id", "provider_id", "display_name", "routes", "destinations"})
            self.assertNotIn(owner["id"], all_ids)
            all_ids.add(owner["id"])
            local = {route["id"] for route in owner["routes"]}
            for route in owner["routes"]:
                if route["id"] in routes:
                    self.assertEqual(route["id"], "forward-proxy")
                    self.assertEqual(routes[route["id"]], route)
                routes[route["id"]] = route
            for destination in owner["destinations"]:
                key = (destination["kind"], destination["id"])
                self.assertNotIn(key, destinations)
                destinations.add(key)
                self.assertEqual(destination["provider_id"], owner["provider_id"])
                self.assertLessEqual(set(destination["endpoint_ids"]), local)
                self.assertLessEqual(set(destination["profile_ids"]), profiles)
                self.assertEqual(len(destination["endpoint_ids"]), len(set(destination["endpoint_ids"])))
                self.assertEqual(len(destination["profile_ids"]), len(set(destination["profile_ids"])))

    def test_duplicate_modes_and_profiles_are_rejected(self):
        for field, duplicate in [("credentialSources", "company"), ("profiles", "openai-responses-v1")]:
            values = binding_values()
            values["external-api-providers"][0][field] = [duplicate, duplicate]
            self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)
        for sources in [[], ["unknown"], ["global", "global"], ["override", "override"],
                        ["global", "company"], ["override", "caller_supplied"],
                        ["global", "none"], ["override", "none"]]:
            with self.subTest(sources=sources):
                values = binding_values()
                values["external-api-providers"][0]["credentialSources"] = sources
                self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)

    def test_credential_source_aliases_preserve_full_render_and_revisions(self):
        for canonical, aliases in [
            (["global"], ["company"]),
            (["override"], ["caller_supplied"]),
            (["override", "global"], ["caller_supplied", "company"]),
            (["override", "global"], ["override", "company"]),
            (["global", "override"], ["global", "caller_supplied"]),
        ]:
            with self.subTest(aliases=aliases):
                values = binding_values()
                provider = values["external-api-providers"][0]
                provider["credentialSources"] = canonical
                if "global" in canonical:
                    provider["localReroute"] = "local-chat"
                else:
                    provider.pop("authSecret")
                canonical_render = render("agentgateway", values).stdout
                provider["credentialSources"] = aliases
                # Includes catalog, revision hashes, permissions, Secret refs and reroutes.
                self.assertEqual(render("agentgateway", values).stdout, canonical_render)

    def test_provisioning_and_frozen_caller_choice(self):
        for credential in [None, "", "synthetic-provider-token"]:
            values = binding_values()
            inputs = values["infraAgentgatewayWrapperSecrets"]["providerCredentials"]
            if credential is None:
                inputs.pop("example-openai-key")
            else:
                inputs["example-openai-key"] = credential
            rendered = render("agentgateway", values)
            owner = next(entry for entry in catalog(rendered)["external-api-providers"] if entry["id"] == "openai")
            modes = {mode["source"] for mode in owner["destinations"][0]["credential_modes"]}
            self.assertEqual("company" in modes, bool(credential))
            self.assertIn("caller_supplied", modes)
            processing = resource(rendered, "AgentgatewayPolicy", "ai-openai-api-processing")["spec"]["traffic"]
            expression = processing["extProc"]["conditional"][0]["policy"]["metadataContext"]["neurwerk.destination_policy"]["request_context"]
            self.assertIn('"authorization" in metadata.neurwerk_identity.provider_headers ? "caller_supplied"', expression)
            # Empty/malformed values still count as supplied; no response or token
            # validity can cause company selection. extProc validates their syntax.
            self.assertNotIn("request.headers", expression)
            self.assertNotIn("response", expression)
            route = processing["authorization"]["policy"]["matchExpressions"][0]
            self.assertIn('["caller_supplied","company"]' if credential else '["caller_supplied"]', route)
            identity = {
                "contract_version": 1, "request_id": "request", "principal_id": "user",
                "credential_context_version": "1", "credential_id": "key", "credential_kind": "personal",
                "entry_mode": "api", "policy_revision": catalog(rendered)["policy_revision"],
                "path": "/bindings/openai/v1/chat/completions", "method": "POST", "standard_oidc": False,
                "permissions": ["llm:invoke", "model:example-chat:invoke", "provider:openai:company-use"],
            }
            env = Environment()
            program = env.program(env.compile(expression))
            for headers, counts, admitted in [
                ([], {}, bool(credential)),
                (["authorization"], {"authorization": 1}, True),
                (["authorization"], {"authorization": 2}, False),
                (["authorization", "x-api-key"], {"authorization": 1, "x-api-key": 1}, False),
                (["x-api-key"], {"x-api-key": 1}, False),
            ]:
                identity.update(provider_headers=headers, provider_header_counts=counts)
                context = {"jwt": {}, "metadata": {"neurwerk_identity": identity}}
                self.assertEqual(self.evaluate(route, context), admitted)
                frozen = program.evaluate({"metadata": json_to_cel(context["metadata"])})
                if "authorization" in headers:
                    self.assertEqual(frozen["provider_credential_source"], "caller_supplied")
            identity.update(provider_headers=[], provider_header_counts={}, permissions=["llm:invoke"])
            self.assertFalse(self.evaluate(route, {"jwt": {}, "metadata": {"neurwerk_identity": identity}}))
        for credential in [" ", "token with space", " token", "token\n", {}, [], 12, '{"key":"value"}']:
            values = binding_values()
            values["infraAgentgatewayWrapperSecrets"]["providerCredentials"]["example-openai-key"] = credential
            self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)
        values = binding_values()
        values["external-api-providers"][0]["credentialSources"] = ["global"]
        values["infraAgentgatewayWrapperSecrets"]["providerCredentials"]["example-openai-key"] = ""
        result = catalog(render("agentgateway", values))
        owner = next(owner for owner in result["external-api-providers"] if owner["id"] == "openai")
        self.assertEqual(owner["destinations"][0]["credential_modes"], [])

    def test_group_classification_does_not_change_backend_auth(self):
        values = binding_values()
        provider = values["external-api-providers"].pop(0)
        values["self-hosted-api-providers"].append(provider)
        rendered = render("agentgateway", values)
        owner = next(entry for entry in catalog(rendered)["self-hosted-api-providers"] if entry["id"] == "openai")
        self.assertEqual({mode["source"] for mode in owner["destinations"][0]["credential_modes"]},
                         {"company", "caller_supplied"})
        company = next(doc for doc in documents(rendered) if doc["kind"] == "AgentgatewayModel"
                       and doc["metadata"]["name"].startswith("ai-openai-") and doc["metadata"]["name"].endswith("-company"))
        self.assertEqual(company["spec"]["policies"]["auth"]["secretRef"], {"name": "example-openai-key"})

    def test_discovery_scope_and_bridge_inputs(self):
        values = binding_values()
        values["self-hosted-api-providers"][0]["routes"]["api"]["path"] = "/local/api"
        values["external-api-providers"][1]["enabled"] = False
        values["aiGateway"]["approvedOidcClients"] = [
            {"clientId": "addon-one", "bindings": ["openai"]},
            {"clientId": "addon-two", "bindings": ["local"]},
        ]
        rendered = render("agentgateway", values)
        for binding, prefix in [("openai", "/bindings/openai"), ("local", "/local/api")]:
            route = resource(rendered, "HTTPRoute", f"ai-{binding}-model-discovery")
            first, second = route["spec"]["rules"]
            self.assertEqual(first["matches"][0]["path"]["value"], prefix + "/v1/models")
            self.assertEqual(first["filters"][0]["urlRewrite"]["path"]["replaceFullPath"],
                             f"/gateway/model-discovery/{binding}/models")
            self.assertEqual(second["filters"][0]["urlRewrite"]["path"]["replacePrefixMatch"],
                             f"/gateway/model-discovery/{binding}/models")
            expression = resource(rendered, "AgentgatewayPolicy", f"ai-{binding}-model-discovery-processing")["spec"]["traffic"]["authorization"]["policy"]["matchExpressions"][0]
            self.assertIn(prefix + "/v1/models/", expression)
            self.assertIn('metadata.neurwerk_identity.method == "GET"', expression)
            self.assertNotIn("model:model/", expression)
        bridge = render("keycloak-api-key-bridge", values)
        mapping = json.loads(env_value(bridge, "KEYCLOAK_API_KEY_BRIDGE_GATEWAY_CALLER_OIDC_CLIENTS_BY_BINDING"))
        self.assertEqual(mapping, {"openai": ["addon-one"], "local": ["addon-two"], "openrouter": []})
        env = resource(bridge, "Deployment", "auth-keycloak-api-key-bridge-deployment")["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertFalse(any(item["name"] in {
            "KEYCLOAK_API_KEY_BRIDGE_GATEWAY_CALLER_OIDC_BINDINGS",
            "KEYCLOAK_API_KEY_BRIDGE_GATEWAY_DISCOVERY_BINDINGS",
        } for item in env))

    def test_capacity_and_constant_shared_policy(self):
        shared = None
        for count in [9, 33, 256]:
            rendered = render("agentgateway", capacity_values(count))
            result = catalog(rendered)
            ids = {route["id"] for owner in catalog_owners(result) for route in owner["routes"]}
            self.assertEqual(len(ids), count)
            edge = resource(rendered, "HTTPRoute", "infra-agentgateway-external-httproute")["spec"]
            self.assertLessEqual(len(edge["rules"]), 16)
            self.assertTrue(all(len(rule["matches"]) <= 64 for rule in edge["rules"]))
            self.assertEqual(sum(len(rule["matches"]) for rule in edge["rules"]), count)
            traffic = resource(rendered, "AgentgatewayPolicy", "ai-admission-api-bindings")["spec"]["traffic"]
            current = (traffic["authorization"], traffic["transformation"])
            # Only revision literals vary; expressions and branch/action counts do not.
            normalized = json.dumps(current).replace(result["policy_revision"], "REVISION")
            if shared is not None:
                self.assertEqual(shared, normalized)
            shared = normalized
            self.assertEqual(len(traffic["transformation"]["conditional"]), 4)
            for branch in traffic["transformation"]["conditional"]:
                self.assertLessEqual(len(branch["condition"]), 16384)
                for direction in ["request", "response"]:
                    for action in ["add", "set", "remove"]:
                        self.assertLessEqual(len(branch["policy"][direction].get(action, [])), 16)
            self.assertLess(len(json.dumps(result).encode()), 1048576)
        self.assertNotEqual(render("agentgateway", capacity_values(257), check=False).returncode, 0)
        # 256 API endpoints plus the shared proxy is independently over capacity.
        rejected = render("agentgateway", capacity_values(256, inspected=1), check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("256 unique endpoints", rejected.stderr)

    def test_listener_reservation(self):
        rendered = render("agentgateway", capacity_values(62, inspected=62))
        self.assertEqual(len(resource(rendered, "Gateway", "infra-agentgateway-gateway")["spec"]["listeners"]), 64)
        rejected = render("agentgateway", capacity_values(63, inspected=63), check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("62 providers", rejected.stderr)
