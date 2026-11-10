"""Grouped ownership, scoped discovery and bounded native policy growth."""

import copy
import json
import unittest

from celpy import Environment, celtypes, json_to_cel
from celpy.adapter import CELJSONEncoder

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
        "credentialSources": ["override"],
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

    def test_model_modes_limit_catalog_and_native_targets(self):
        values = binding_values()
        binding = values["external-api-providers"][0]
        binding["models"] = [
            {"name": "global-chat", "upstreamModel": "global-upstream", "credentialSources": ["global"]},
            {"name": "override-chat", "upstreamModel": "override-upstream", "credentialSources": ["override"]},
        ]
        values["authKeycloak"]["agentgatewayClientRoles"].extend(
            ["model:global-chat:invoke", "model:override-chat:invoke"])
        rendered = render("agentgateway", values)
        owner = next(entry for entry in catalog(rendered)["external-api-providers"] if entry["id"] == "openai")
        for model, allowed in [("global-chat", "global"), ("override-chat", "override")]:
            destination = next(item for item in owner["destinations"] if item["id"] == model)
            self.assertEqual([mode["source"] for mode in destination["credential_modes"]], [allowed])
            virtual = next(doc for doc in documents(rendered) if doc["kind"] == "AgentgatewayModel"
                           and "virtualModel" in doc["spec"] and doc["spec"]["match"]["model"] == model)
            targets = virtual["spec"]["virtualModel"]["conditional"]["targets"]
            for source in ["global", "override"]:
                context = {"metadata": {"neurwerk_admission": {"provider_credential_source": source}},
                           "extproc": {"neurwerk.request_context": {
                               "dispatch_destination_id": model, "dispatch_binding_id": "openai"}}}
                self.assertEqual(any(CredentialModeTests.value(target["when"], context) for target in targets),
                                 source == allowed)
        binding["models"][0]["credentialSources"] = ["none"]
        self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)

    def test_studio_cors_is_exact_and_attached_to_every_external_route(self):
        values = capacity_values(65)
        values["frontendStudio"] = {"studio": {"hostname": "studio.example.test"}}
        rendered = render("agentgateway", values)
        middleware = resource(rendered, "Middleware", "infra-agentgateway-studio-cors")
        headers = middleware["spec"]["headers"]
        self.assertEqual(headers["accessControlAllowOriginList"], ["https://studio.example.test"])
        self.assertIs(headers["accessControlAllowCredentials"], False)
        self.assertEqual(headers["accessControlAllowMethods"], ["GET", "POST", "DELETE", "OPTIONS"])
        self.assertIn("Authorization-Neurwerk", headers["accessControlAllowHeaders"])
        self.assertNotIn("X-Neurwerk-Credential-Mode", headers["accessControlAllowHeaders"])
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
            fields = {"id", "provider_id", "display_name", "routes", "destinations"}
            if owner["destinations"][0]["kind"] == "model":
                fields.add("global_credential_status")
            self.assertEqual(set(owner), fields)
            self.assertNotIn(owner["id"], all_ids)
            all_ids.add(owner["id"])
            local = {route["id"] for route in owner["routes"]}
            for route in owner["routes"]:
                if route["entry_mode"] == "api":
                    self.assertEqual(route["identity_header"], "Authorization-Neurwerk" if route["provider_credential_source"] == "override" else "Authorization")
                else:
                    self.assertNotIn("credential_mode_header", route)
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
        for field, duplicate in [("credentialSources", "global"), ("profiles", "openai-responses-v1")]:
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

    def test_legacy_credential_sources_are_rejected(self):
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
                render("agentgateway", values)
                provider["credentialSources"] = aliases
                self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)

    def test_provisioning_and_frozen_caller_choice(self):
        revisions = {}
        for credential in [None, "", "synthetic-provider-token", "rotated-provider-token"]:
            values = binding_values()
            inputs = values["infraAgentgatewayWrapperSecrets"]["providerCredentials"]
            if credential is None:
                inputs.pop("example-openai-key")
            else:
                inputs["example-openai-key"] = credential
            rendered = render("agentgateway", values)
            result = catalog(rendered)
            owner = next(entry for entry in result["external-api-providers"] if entry["id"] == "openai")
            self.assertEqual(owner["global_credential_status"], "configured" if credential else "missing")
            self.assertEqual(result["self-hosted-api-providers"][0]["global_credential_status"], "disabled")
            self.assertNotIn("company_credential_status", json.dumps(result))
            if credential:
                self.assertNotIn(credential, json.dumps(result))
            revisions[credential] = (result["catalog_revision"], result["policy_revision"])
            modes = {mode["source"] for mode in owner["destinations"][0]["credential_modes"]}
            self.assertEqual("global" in modes, bool(credential))
            self.assertIn("override", modes)
            processing = resource(rendered, "AgentgatewayPolicy", "ai-openai-api-processing")["spec"]["traffic"]
            expression = processing["extProc"]["conditional"][0]["policy"]["metadataContext"]["neurwerk.destination_policy"]["request_context"]
            self.assertIn('metadata.neurwerk_identity.provider_credential_source', expression)
            # Empty/malformed values still count as supplied; no response or token
            # validity can cause global selection. extProc validates their syntax.
            self.assertNotIn("request.headers", expression)
            self.assertNotIn("response", expression)
            route = processing["authorization"]["policy"]["matchExpressions"][0]
            self.assertIn('["override","global"]' if credential else '["override"]', route)
            identity = {
                "contract_version": 1, "request_id": "request", "principal_id": "user",
                "credential_context_version": "1", "credential_id": "key", "credential_kind": "personal",
                "entry_mode": "api", "policy_revision": catalog(rendered)["policy_revision"],
                "path": "/bindings/openai/v1/chat/completions", "method": "POST", "standard_oidc": False,
                "permissions": ["llm:invoke", "model:example-chat:invoke", "provider:openai:global-use"],
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
                identity.update(provider_headers=headers, provider_header_counts=counts,
                                provider_credential_source="override" if headers else "global")
                selected_route = route
                if headers:
                    selected_route = resource(rendered, "AgentgatewayPolicy", "ai-openai-api-override-processing")["spec"]["traffic"]["authorization"]["policy"]["matchExpressions"][0]
                identity["path"] = ("/override" if headers else "") + "/bindings/openai/v1/chat/completions"
                context = {"jwt": {}, "metadata": {"neurwerk_identity": identity}}
                self.assertEqual(self.evaluate(selected_route, context), admitted)
                frozen = program.evaluate({"metadata": json_to_cel(context["metadata"])})
                if "authorization" in headers:
                    self.assertEqual(frozen["provider_credential_source"], "override")
            identity.update(provider_headers=[], provider_header_counts={}, permissions=["llm:invoke"])
            self.assertFalse(self.evaluate(route, {"jwt": {}, "metadata": {"neurwerk_identity": identity}}))
        self.assertEqual(revisions[None], revisions[""])
        self.assertEqual(revisions["synthetic-provider-token"], revisions["rotated-provider-token"])
        self.assertNotEqual(revisions[None][0], revisions["synthetic-provider-token"][0])
        for credential in [" ", "token with space", " token", "token\n", {}, [], 12, '{"key":"value"}']:
            values = binding_values()
            values["infraAgentgatewayWrapperSecrets"]["providerCredentials"]["example-openai-key"] = credential
            self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)
        values = binding_values()
        values["external-api-providers"][0]["credentialSources"] = ["global"]
        values["infraAgentgatewayWrapperSecrets"]["providerCredentials"]["example-openai-key"] = ""
        result = catalog(render("agentgateway", values))
        owner = next(owner for owner in result["external-api-providers"] if owner["id"] == "openai")
        self.assertEqual(owner["global_credential_status"], "missing")
        self.assertEqual(owner["destinations"][0]["credential_modes"], [])
        values["external-api-providers"][0]["credentialSources"] = ["override"]
        values["external-api-providers"][0].pop("authSecret")
        values["mcp-servers"] = {"enabled": True, "contextforgeRoutesEnabled": ["context7"],
                                 "catalog": {"presets": {"context7": {"enabled": True}}}}
        values["authKeycloak"]["agentgatewayClientRoles"].append("mcp:context7:invoke")
        result = catalog(render("agentgateway", values))
        owner = next(owner for owner in result["external-api-providers"] if owner["id"] == "openai")
        self.assertEqual(owner["global_credential_status"], "disabled")
        self.assertEqual([mode["source"] for mode in owner["destinations"][0]["credential_modes"]],
                         ["override"])
        self.assertTrue(result["mcp-servers"])
        for owner in result["mcp-servers"]:
            self.assertNotIn("global_credential_status", owner)

    def test_group_classification_does_not_change_backend_auth(self):
        values = binding_values()
        provider = values["external-api-providers"].pop(0)
        values["self-hosted-api-providers"].append(provider)
        rendered = render("agentgateway", values)
        owner = next(entry for entry in catalog(rendered)["self-hosted-api-providers"] if entry["id"] == "openai")
        self.assertEqual({mode["source"] for mode in owner["destinations"][0]["credential_modes"]},
                         {"global", "override"})
        global_mode = next(doc for doc in documents(rendered) if doc["kind"] == "AgentgatewayModel"
                       and doc["metadata"]["name"].startswith("ai-openai-") and doc["metadata"]["name"].endswith("-global"))
        self.assertEqual(global_mode["spec"]["policies"]["auth"]["secretRef"], {"name": "example-openai-key"})

    def test_discovery_scope_and_bridge_inputs(self):
        values = binding_values()
        values["self-hosted-api-providers"][0]["routes"]["api"]["path"] = "/local/api"
        values["external-api-providers"][1]["enabled"] = False
        codex = {
            "id": "codex", "enabled": True, "providerId": "openai",
            "upstreamURL": "https://chatgpt.com/backend-api/codex",
            "credentialSources": ["override"], "profiles": ["openai-codex-responses-v1"],
            "routes": {"api": {"enabled": True}, "inspected": {"enabled": False}},
            "models": [{"name": "codex-chat", "upstreamModel": "codex-upstream"}],
        }
        values["external-subscription-providers"] = [codex]
        values["authKeycloak"]["agentgatewayClientRoles"].append("model:codex-chat:invoke")
        values["aiGateway"]["approvedOidcClients"] = [
            {"clientId": "addon-one", "bindings": ["openai"]},
            {"clientId": "addon-two", "bindings": ["local"]},
            {"clientId": "addon-three", "bindings": ["codex"]},
        ]
        rendered = render("agentgateway", values)
        for binding, prefix in [("openai", "/bindings/openai"), ("local", "/local/api"),
                                ("codex", "/bindings/codex")]:
            route = resource(rendered, "HTTPRoute", f"ai-{binding}-model-discovery")
            first, second = route["spec"]["rules"]
            self.assertEqual(first["matches"][0]["path"]["value"], prefix + "/v1/models")
            self.assertEqual(first["filters"][0]["urlRewrite"]["path"]["replaceFullPath"],
                             f"/gateway/model-discovery/api-{binding}/models")
            self.assertEqual(second["filters"][0]["urlRewrite"]["path"]["replacePrefixMatch"],
                             f"/gateway/model-discovery/api-{binding}/models")
            expression = resource(rendered, "AgentgatewayPolicy", f"ai-{binding}-model-discovery-processing")["spec"]["traffic"]["authorization"]["policy"]["matchExpressions"][0]
            self.assertIn(prefix + "/v1/models/", expression)
            self.assertIn('metadata.neurwerk_identity.method == "GET"', expression)
            self.assertNotIn("model:model/", expression)
        bridge = render("keycloak-api-key-bridge", values)
        mapping = json.loads(env_value(bridge, "KEYCLOAK_API_KEY_BRIDGE_GATEWAY_CALLER_OIDC_CLIENTS_BY_BINDING"))
        self.assertEqual(mapping, {"openai": ["addon-one"], "local": ["addon-two"], "openrouter": [],
                                   "codex": ["addon-three"]})
        env = resource(bridge, "Deployment", "auth-keycloak-api-key-bridge-deployment")["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertFalse(any(item["name"] in {
            "KEYCLOAK_API_KEY_BRIDGE_GATEWAY_CALLER_OIDC_BINDINGS",
            "KEYCLOAK_API_KEY_BRIDGE_GATEWAY_DISCOVERY_BINDINGS",
        } for item in env))
        for enabled, api_enabled in [(False, True), (True, False)]:
            with self.subTest(enabled=enabled, api_enabled=api_enabled):
                codex["enabled"] = enabled
                codex["routes"]["api"]["enabled"] = api_enabled
                bridge = render("keycloak-api-key-bridge", values)
                mapping = json.loads(env_value(
                    bridge, "KEYCLOAK_API_KEY_BRIDGE_GATEWAY_CALLER_OIDC_CLIENTS_BY_BINDING"))
                self.assertEqual(mapping, {"openai": ["addon-one"], "local": ["addon-two"], "openrouter": []})

    def test_capacity_and_constant_shared_policy(self):
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
            self.assertLessEqual(len(traffic["transformation"]["conditional"]), 16)
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

    def test_one_hundred_models_keep_native_dispatch_within_expression_limits(self):
        values = capacity_values(1)
        binding = values["external-api-providers"][0]
        binding["models"] = [{"name": f"remote/openrouter/vendor-name/model-family-2026-{index:03}", "upstreamModel": f"vendor-name/model-family-2026-{index:03}"}
                             for index in range(100)]
        values["authKeycloak"]["agentgatewayClientRoles"] = ["llm:invoke", *[
            f"model:{model['name']}:invoke" for model in binding["models"]]]
        rendered = render("agentgateway", values)
        self.assertEqual(len(resource(rendered, "Gateway", "infra-agentgateway-gateway")["spec"]["listeners"]), 1)
        models = [doc for doc in documents(rendered) if doc["kind"] == "AgentgatewayModel"
                  and doc["spec"]["visibility"] == "Public"]
        self.assertEqual(len(models), 100)
        for model in models:
            self.assertTrue(model["spec"]["virtualModel"]["conditional"]["targets"])
        route = resource(rendered, "HTTPRoute", "ai-provider-0-api-override")
        self.assertEqual(len(route["spec"]["rules"]), 1)
        self.assertEqual(route["spec"]["rules"][0]["name"], "models")
        for document in documents(rendered):
            if document["kind"] != "AgentgatewayPolicy":
                continue
            traffic = document["spec"].get("traffic", {})
            branches = traffic.get("transformation", {}).get("conditional", [])
            self.assertLessEqual(len(branches), 16)
            for branch in branches:
                self.assertLessEqual(len(branch["condition"]), 16384)
                for direction in ["request", "response"]:
                    policy = branch["policy"].get(direction, {})
                    self.assertLessEqual(len(policy.get("metadata", {})), 16)
                    self.assertLessEqual(len(policy.get("remove", [])), 16)
            for branch in traffic.get("extProc", {}).get("conditional", []):
                for namespace in branch["policy"]["metadataContext"].values():
                    for expression in namespace.values():
                        self.assertLessEqual(len(expression), 16384)


class CredentialModeTests(unittest.TestCase):
    """Evaluate the rendered admission → stripping → route/dispatch chain."""

    def test_local_stream_timestamp_setting_reaches_direct_and_rerouted_dispatch(self):
        for enabled in (False, True):
            values = binding_values()
            values["external-api-providers"][0]["localReroute"] = "local-chat"
            if enabled:
                values["self-hosted-api-providers"][0]["normalizeStreamCreated"] = True
            rendered = render("agentgateway", values)
            for binding in ("local", "openai"):
                policy = resource(rendered, "AgentgatewayPolicy", f"ai-{binding}-api-processing")
                branches = policy["spec"]["traffic"]["extProc"]["conditional"]
                for branch in branches:
                    metadata = branch["policy"]["metadataContext"]["neurwerk.destination_policy"]
                    models = self.value(metadata["model_bindings"], {})
                    targets = self.value(metadata["reroute_targets"], {})
                    if binding == "local":
                        self.assertIs(models["local-chat"]["normalize_stream_created"], enabled)
                    else:
                        self.assertIs(models["example-chat"]["normalize_stream_created"], False)
                        self.assertIs(targets["local-chat"]["normalize_stream_created"], enabled)

    def test_stream_timestamp_setting_rejects_invalid_type_and_remote_backend(self):
        for group, setting in (("self-hosted-api-providers", "true"),
                               ("self-hosted-api-providers", 1),
                               ("external-api-providers", True)):
            values = binding_values()
            values[group][0]["normalizeStreamCreated"] = setting
            with self.subTest(group=group, setting=setting):
                self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)

    @classmethod
    def setUpClass(cls):
        cls.values = binding_values()
        cls.values["external-api-providers"][0]["localReroute"] = "local-chat"
        cls.values["mcp-servers"] = {
            "enabled": True, "contextforgeRoutesEnabled": ["context7"],
            "catalog": {"presets": {"context7": {"enabled": True}}},
        }
        cls.values["authKeycloak"]["agentgatewayClientRoles"].append("mcp:context7:invoke")
        cls.rendered = render("agentgateway", cls.values)

    @staticmethod
    def value(expression, context):
        env = Environment()
        program = env.program(env.compile(expression), functions={
            "raw": lambda headers: celtypes.MapType({
                key: value if isinstance(value, celtypes.ListType) else celtypes.ListType([value])
                for key, value in headers.items()
            }),
            "uuid": lambda: celtypes.StringType("synthetic-request"),
            "substring": lambda value, start: celtypes.StringType(value[int(start):]),
        })
        result = program.evaluate({key: json_to_cel(value) for key, value in context.items()})
        return json.loads(json.dumps(result, cls=CELJSONEncoder))

    def chain(self, lines, *, binding="openai", discovery=False, roles=None, rendered=None, dedicated=True,
              oidc=False, operation="chat/completions", source=None, codex=False, token_type="Bearer",
              extproc_mutation=None):
        rendered = rendered or self.rendered
        mcp = binding == "mcp-context7"
        source = source or ("none" if mcp or binding == "local" else "global")
        owner = next(owner for owner in catalog_owners(catalog(rendered)) if owner["id"] == binding)
        endpoint = next((route for route in owner["routes"] if route["entry_mode"] == "api"
                         and route["provider_credential_source"] == source), None)
        if endpoint is None:
            return False, {}
        path = "/" + endpoint["url"].split("/", 3)[3]
        if not mcp:
            path += "/" + ("models" if discovery else operation)
        headers = {}
        for name, value in lines:
            name = name.lower()
            if name in headers:
                previous = headers[name]
                headers[name] = [*previous, value] if isinstance(previous, list) else [previous, value]
            else:
                headers[name] = value
        if dedicated:
            headers.setdefault("authorization-neurwerk" if source == "override" else "authorization", "Bearer synthetic-platform")
        context = {
            "jwt": ({"sub": "synthetic-user", "typ": token_type, "azp": "agentgateway", "resource_access": {
                "agentgateway": {"roles": roles if roles is not None else [
                    "llm:invoke", "model:example-chat:invoke", "model:local-chat:invoke",
                    "provider:openai:global-use", "mcp:context7:invoke"]}}} if oidc else {}), "extauthz": {
                "contract_version": 1, "principal_id": "synthetic-user", "request_id": "synthetic-request",
                "credential_id": "synthetic-key", "credential_kind": "personal", "account_email": None,
                "permissions": roles if roles is not None else [
                    "llm:invoke", "model:example-chat:invoke", "model:local-chat:invoke",
                    "provider:openai:global-use", "mcp:context7:invoke",
                ],
            },
            "request": {"path": path, "method": "GET" if discovery else "POST", "headers": headers},
            "metadata": {},
        }
        traffic = resource(rendered, "AgentgatewayPolicy", "ai-admission-api-bindings")["spec"]["traffic"]
        credential = traffic["extAuth"]["conditional"][0]["policy"]["http"]["addRequestHeaders"]["authorization"]
        if not self.value(credential, context):
            return False, context
        if not self.value(traffic["authorization"]["policy"]["matchExpressions"][0], context):
            return False, context
        branches = [branch for branch in traffic["transformation"]["conditional"]
                    if self.value(branch["condition"], context)]
        self.assertEqual(len(branches), 1)
        transform = branches[0]["policy"]["request"]
        for name, expression in transform["metadata"].items():
            context["metadata"][name] = self.value(expression, context)
        for action in transform.get("set", []):
            headers[action["name"]] = self.value(action["value"], context)
        for name in transform.get("remove", []):
            headers.pop(name, None)
        inherited = resource(rendered, "AgentgatewayPolicy", "ai-route-default-deny-api-bindings")["spec"]["traffic"]
        suffix = "-override" if source == "override" and not codex else ""
        name = ("mcp-context7-policy" if mcp else f"ai-{binding}{suffix}-model-discovery-processing"
                if discovery else f"ai-{binding}-api{suffix}-processing")
        route = resource(rendered, "AgentgatewayPolicy", name)["spec"]["traffic"]
        admitted = self.value(route["authorization"]["policy"]["matchExpressions"][0], context)
        if admitted and not discovery:
            # The extProc contract receives the same frozen source after both sensitive
            # headers are gone; it must not infer source from the remaining headers.
            extproc = route["extProc"]
            if "conditional" in extproc:
                extproc = next(branch["policy"] for branch in extproc["conditional"]
                               if self.value(branch["condition"], context))
            request_context = self.value(
                extproc["metadataContext"]["neurwerk.destination_policy"]["request_context"], context)
            # httpproxy.rs applies extProc before transformation and header modifiers
            # within PostRouting. Model trusted response mutations at that boundary.
            if extproc_mutation is not None:
                extproc_mutation(headers)
            transform = route["transformation"]
            if "conditional" in transform:
                transform = next(branch["policy"] for branch in transform["conditional"]
                                 if self.value(branch["condition"], context))
            for name, expression in transform["request"].get("metadata", {}).items():
                context["metadata"][name] = self.value(expression, context)
            for action in transform["request"].get("set", []):
                headers[action["name"]] = self.value(action["value"], context)
            for name in transform["request"].get("remove", []):
                headers.pop(name, None)
            self.assertEqual(request_context, context["metadata"]["neurwerk_admission"])
        if admitted:
            modifiers = route.get("headerModifiers", inherited.get("headerModifiers", {}))
            for name in modifiers.get("request", {}).get("remove", []):
                headers.pop(name, None)
        return admitted, context

    def test_contextforge_identity_is_sanitized_then_preserved_after_extproc(self):
        header = "x-contextforge-account-email"
        # Every listener branch must sanitize caller identity, including branches
        # outside the MCP route exercised below.
        for policy in documents(self.rendered):
            if policy["kind"] != "AgentgatewayPolicy":
                continue
            traffic = policy["spec"].get("traffic", {})
            if traffic.get("phase") != "PreRouting":
                continue
            for branch in traffic.get("transformation", {}).get("conditional", []):
                self.assertIn(header, branch["policy"]["request"]["remove"], policy["metadata"]["name"])

        for oidc in [False, True]:
            for trusted_email in [None, "trusted@example.test"]:
                with self.subTest(oidc=oidc, trusted_email=trusted_email):
                    def mutate(headers):
                        self.assertNotIn(header, headers, "caller identity reached extProc")
                        if trusted_email is not None:
                            headers[header] = trusted_email

                    admitted, context = self.chain([
                        ("X-ContextForge-Account-Email", "spoofed@example.test"),
                        (header, "second-spoof@example.test"),
                    ], binding="mcp-context7", oidc=oidc, extproc_mutation=mutate)
                    self.assertTrue(admitted)
                    self.assertEqual(context["request"]["headers"].get(header), trusted_email)

    def test_legacy_headers_are_ignored_without_identity_fallback(self):
        for lines in [
            [("X-Neurwerk-Credential-Mode", value)]
            for value in ["", " ", "Global", "NONE", "unknown", "global,none", "global,global", "global\n"]
        ] + [[("X-Neurwerk-Credential-Mode", "global"), ("x-neurwerk-credential-mode", "global")],
             [("X-Neurwerk-Authorization", "Bearer ignored-one"), ("x-neurwerk-authorization", "Bearer ignored-two")]]:
            with self.subTest(lines=lines):
                admitted, context = self.chain(lines)
                self.assertTrue(admitted)
                self.assertNotIn("x-neurwerk-credential-mode", context["request"]["headers"])
        self.assertFalse(self.chain([("x-neurwerk-authorization", "Bearer synthetic")], dedicated=False)[0])
        self.assertFalse(self.chain([("authorization-neurwerk", "Bearer one"),
                                     ("Authorization-Neurwerk", "Bearer two"),
                                     ("authorization", "Bearer provider")], source="override")[0])
        admitted, context = self.chain([("authorization-neurwerk", "Bearer one"),
                                       ("Authorization-Neurwerk", "Bearer two")])
        self.assertTrue(admitted)
        self.assertIn("authorization-neurwerk", context["metadata"]["neurwerk_identity"]["stripped_credential_headers"])
        # Explicit identity extraction cannot fall back even to an otherwise valid OIDC bearer.
        expression = resource(self.rendered, "AgentgatewayPolicy", "ai-admission-api-bindings")[
            "spec"]["traffic"]["jwtAuthentication"]["location"]["expression"]
        self.assertEqual(self.value(expression, {"request": {"path": "/override/bindings/openai/v1/models", "headers": {
            "x-neurwerk-credential-mode": "global", "authorization": "Bearer synthetic-oidc",
            "x-neurwerk-authorization": "invalid-platform-key",
        }}}), "")
        self.assertEqual(self.value(expression, {"request": {"path": "/bindings/openai/v1/models", "headers": {
            "x-neurwerk-credential-mode": "global", "authorization": "Bearer synthetic-oidc",
        }}}), "synthetic-oidc")

    def test_global_none_stripping_and_frozen_dispatch(self):
        for binding, mode in [("openai", "global"), ("local", "none"), ("mcp-context7", "none")]:
            with self.subTest(binding=binding):
                admitted, context = self.chain([
                    ("X-NeUrWeRk-CrEdEnTiAl-MoDe", f" \t{mode}\t "),
                    ("authorization", "Bearer incidental"),
                    ("x-agentgateway-auth-context", '{"provider_credential_source":"override"}'),
                ], binding=binding)
                self.assertTrue(admitted)
                headers = context["request"]["headers"]
                for name in ["authorization", "x-neurwerk-authorization", "x-neurwerk-credential-mode",
                             "x-agentgateway-auth-context"]:
                    self.assertNotIn(name, headers)
                self.assertEqual(context["metadata"]["neurwerk_admission"]["provider_credential_source"], mode)
                if binding == "openai":
                    context["extproc"] = {"neurwerk.request_context": {
                        "dispatch_destination_id": "example-chat", "dispatch_binding_id": "openai",
                    }}
                    model = next(doc for doc in documents(self.rendered) if doc["kind"] == "AgentgatewayModel"
                                 and "virtualModel" in doc["spec"] and doc["spec"]["match"]["model"] == "example-chat")
                    targets = [target for target in model["spec"]["virtualModel"]["conditional"]["targets"]
                               if self.value(target["when"], context)]
                    self.assertEqual(len(targets), 1)
                    self.assertTrue(targets[0]["modelRef"]["name"].endswith("-global"))
                    context["extproc"]["neurwerk.request_context"].update(
                        dispatch_destination_id="local-chat", dispatch_binding_id="local")
                    targets = [target for target in model["spec"]["virtualModel"]["conditional"]["targets"]
                               if self.value(target["when"], context)]
                    self.assertEqual(len(targets), 1)
                    self.assertTrue(targets[0]["modelRef"]["name"].endswith("-local-reroute"))

    def test_incidental_bearer_and_unexpected_credentials_fail_closed(self):
        for extra in [
            [("authorization", "")], [("authorization", "Basic incidental")],
            [("authorization", "Bearer")], [("authorization", "Bearer with space")],
            [("authorization", "Bearer one"), ("Authorization", "Bearer two")],
        ]:
            for binding, mode in [("openai", "global"), ("local", "none")]:
                with self.subTest(extra=extra, mode=mode):
                    self.assertFalse(self.chain(extra, binding=binding)[0])
        for header in ["x-api-key", "x-goog-api-key", "api-key", "authorization-neurwerk", "proxy-authorization", "cookie"]:
            admitted, context = self.chain([(header, "ignored")])
            self.assertTrue(admitted)
            self.assertNotIn(header, context["request"]["headers"])

    def test_eligibility_permissions_and_override_never_fall_back(self):
        for binding, mode, extra in [
            ("openai", "override", []), ("openai", "override", [("x-api-key", "synthetic")]),
            ("openai", "override", [("authorization", "Bearer one"), ("Authorization", "Bearer two")]),
            ("openai", "override", [("authorization", "Bearer malformed token")]),
        ]:
            with self.subTest(binding=binding, mode=mode, extra=extra):
                self.assertFalse(self.chain(extra, binding=binding, source=mode)[0])
        for header in ["x-api-key", "x-goog-api-key", "api-key"]:
            admitted, context = self.chain([("authorization", "Bearer synthetic-provider"),
                                           (header, "ignored")], source="override")
            self.assertTrue(admitted)
            self.assertEqual(context["request"]["headers"]["authorization"], "Bearer synthetic-provider")
            self.assertNotIn(header, context["request"]["headers"])
            self.assertIn(header, context["metadata"]["neurwerk_identity"]["stripped_credential_headers"])
        self.assertFalse(self.chain([("x-neurwerk-credential-mode", "global")], roles=["llm:invoke"])[0])
        admitted, context = self.chain([("x-neurwerk-credential-mode", "override"),
                                         ("authorization", "Bearer synthetic-provider")], source="override")
        self.assertTrue(admitted)
        self.assertEqual(context["request"]["headers"]["authorization"], "Bearer synthetic-provider")
        self.assertEqual(context["metadata"]["neurwerk_admission"]["provider_credential_source"], "override")
        context["extproc"] = {"neurwerk.request_context": {
            "dispatch_destination_id": "local-chat", "dispatch_binding_id": "local",
        }}
        model = next(doc for doc in documents(self.rendered) if doc["kind"] == "AgentgatewayModel"
                     and doc["spec"].get("match", {}).get("model") == "example-chat")
        self.assertFalse(any(self.value(target["when"], context)
                             for target in model["spec"]["virtualModel"]["conditional"]["targets"]))
        values = copy.deepcopy(self.values)
        values["infraAgentgatewayWrapperSecrets"]["providerCredentials"].pop("example-openai-key")
        self.assertFalse(self.chain([("x-neurwerk-credential-mode", "global")], rendered=render("agentgateway", values))[0])

    def test_discovery_keeps_identity_and_normalized_mode_without_provider_credential(self):
        for binding, mode in [("openai", "global"), ("openai", "override"), ("local", "none")]:
            with self.subTest(binding=binding, mode=mode):
                admitted, context = self.chain([("x-neurwerk-credential-mode", f"\t{mode} ")],
                                                 binding=binding, discovery=True, source=mode)
                self.assertTrue(admitted)
                self.assertNotIn("x-neurwerk-credential-mode", context["request"]["headers"])
                self.assertEqual(context["request"]["headers"]["authorization"], "Bearer synthetic-platform")
                admitted, context = self.chain([
                    ("x-neurwerk-credential-mode", mode), ("authorization", "Bearer synthetic-provider"),
                ], binding=binding, discovery=True, source=mode)
                self.assertTrue(admitted)
                self.assertIn("authorization", context["request"]["headers"])
                if mode == "override":
                    self.assertEqual(context["request"]["headers"]["authorization"], "Bearer synthetic-platform")
        self.assertTrue(self.chain([("x-neurwerk-credential-mode", "none")], discovery=True)[0])
        self.assertFalse(self.chain([("x-neurwerk-credential-mode", "global")], discovery=True, roles=["llm:invoke"])[0])
        self.assertTrue(self.chain([("x-neurwerk-credential-mode", "override"), ("api-key", "synthetic")], discovery=True)[0])
        self.assertTrue(self.chain([("x-neurwerk-credential-mode", "override"),
                                    ("Cookie", "synthetic=value")], discovery=True)[0])

    def test_no_mode_header_is_required(self):
        for binding, extra in [("openai", []), ("local", []),
                               ("openai", [("authorization", "Bearer synthetic-provider")])]:
            self.assertTrue(self.chain(extra, binding=binding)[0])
        for binding, discovery in [("mcp-context7", False), ("openai", True)]:
            admitted, _ = self.chain([], binding=binding, discovery=discovery)
            self.assertTrue(admitted)
        self.assertFalse(self.chain([], discovery=True, dedicated=False)[0])

    def test_oidc_mode_keeps_identity_for_discovery_and_strips_it_for_inference(self):
        self.assertFalse(self.chain([], oidc=True, discovery=True, token_type="ID")[0])
        for discovery in [False, True]:
            admitted, context = self.chain([
                ("authorization", "Bearer synthetic-oidc"),
                ("x-neurwerk-credential-mode", "global"),
            ], dedicated=False, oidc=True, discovery=discovery)
            self.assertTrue(admitted)
            self.assertEqual(context["metadata"]["neurwerk_identity"]["provider_credential_source"], "global")
            self.assertEqual("authorization" in context["request"]["headers"], discovery)
        self.assertTrue(self.chain([("authorization", "Bearer synthetic-oidc"),
                                     ("x-neurwerk-credential-mode", "override")],
                                    dedicated=False, oidc=True)[0])
        self.assertTrue(self.chain([("authorization", "Bearer synthetic-oidc")],
                                   binding="mcp-context7", dedicated=False, oidc=True)[0])
        self.assertTrue(self.chain([("authorization", "Bearer synthetic-oidc")],
                                   discovery=True, dedicated=False, oidc=True)[0])

    def test_exact_anthropic_header_and_codex_oauth_are_preserved(self):
        for lines, expected in [
            ([("x-api-key", "synthetic-anthropic")], True),
            ([("authorization", "Bearer synthetic-provider")], False),
        ]:
            admitted, context = self.chain([("x-neurwerk-credential-mode", "override"), *lines],
                                             binding="anthropic", operation="messages", source="override")
            self.assertEqual(admitted, expected)
            if admitted:
                self.assertEqual(context["request"]["headers"]["x-api-key"], "synthetic-anthropic")
        values = copy.deepcopy(self.values)
        codex = values["external-api-providers"].pop(0)
        codex.update(upstreamURL="https://chatgpt.com/backend-api/codex", credentialSources=["override"],
                     profiles=["openai-codex-responses-v1"], billingMode="included")
        codex.pop("authSecret")
        codex.pop("localReroute")
        values["external-subscription-providers"] = [codex]
        rendered = render("agentgateway", values)
        for selector in [[("x-neurwerk-credential-mode", "override")]]:
            admitted, context = self.chain([
                *selector, ("authorization", "Bearer synthetic.oauth.token"),
                ("chatgpt-account-id", "synthetic-account"), ("openai-beta", "responses=experimental"),
            ], rendered=rendered, operation="responses", source="override", codex=True)
            self.assertTrue(admitted)
            self.assertEqual(context["request"]["headers"]["authorization"], "Bearer synthetic.oauth.token")
            self.assertEqual(context["request"]["headers"]["chatgpt-account-id"], "synthetic-account")
        self.assertFalse(self.chain([("x-neurwerk-credential-mode", "global")], rendered=rendered,
                                    operation="responses", source="override", codex=True)[0])

    def test_stripping_log_fields_only_use_bounded_trusted_metadata(self):
        admitted, context = self.chain([("x-neurwerk-credential-mode", "global"),
                                         ("authorization", "Bearer never-log-this-value")])
        self.assertTrue(admitted)
        fields = resource(self.rendered, "AgentgatewayParameters", "infra-agentgateway-parameters")[
            "spec"]["rawConfig"]["frontendPolicies"]["accessLog"]["add"]
        safe = {key: self.value(fields[key], context) for key in [
            "neurwerk.stripped_credential_headers", "neurwerk.credential_mode",
            "neurwerk.credential_strip_reason", "neurwerk.request_id",
        ]}
        self.assertEqual(safe, {
            "neurwerk.stripped_credential_headers": ["x-neurwerk-credential-mode"],
            "neurwerk.credential_mode": "global", "neurwerk.credential_strip_reason": "warning: ignored credential headers",
            "neurwerk.request_id": "synthetic-request",
        })
        self.assertNotIn("never-log-this-value", json.dumps(safe))
        for extra, expected_removed, expected_admitted in [
            ([], ["x-neurwerk-credential-mode"], True),
            ([("authorization", "Bearer never-log-this-value")], ["x-neurwerk-credential-mode", "authorization"], True),
            ([("x-api-key", "never-log-this-value")], ["x-neurwerk-credential-mode", "x-api-key"], True),
        ]:
            with self.subTest(discovery_credentials=extra):
                admitted, discovery_context = self.chain([
                    ("x-neurwerk-credential-mode", " \toverride "), *extra,
                ], discovery=True, source="override")
                self.assertEqual(admitted, expected_admitted)
                removed = self.value(fields["neurwerk.stripped_credential_headers"], discovery_context)
                self.assertEqual(removed, expected_removed)
                self.assertNotIn("x-neurwerk-credential-mode", discovery_context["request"]["headers"])
                for name in removed:
                    if name != "authorization":
                        self.assertNotIn(name, discovery_context["request"]["headers"])
                self.assertNotIn("never-log-this-value", json.dumps(removed))
        context["metadata"] = {}
        self.assertEqual(self.value(fields["neurwerk.stripped_credential_headers"], context), [])
        for key in ["neurwerk.credential_mode", "neurwerk.credential_strip_reason"]:
            self.assertIsNone(self.value(fields[key], context))
