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
        self.assertIn("X-Neurwerk-Credential-Mode", headers["accessControlAllowHeaders"])
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
                    self.assertEqual(route["credential_mode_header"], "X-Neurwerk-Credential-Mode")
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
            self.assertIn('"authorization" in metadata.neurwerk_identity.provider_headers ? "override"', expression)
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
                identity.update(provider_headers=headers, provider_header_counts=counts)
                context = {"jwt": {}, "metadata": {"neurwerk_identity": identity}}
                self.assertEqual(self.evaluate(route, context), admitted)
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
            self.assertEqual(len(traffic["transformation"]["conditional"]), 6)
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


class CredentialModeTests(unittest.TestCase):
    """Evaluate the rendered admission → stripping → route/dispatch chain."""

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
        })
        result = program.evaluate({key: json_to_cel(value) for key, value in context.items()})
        return json.loads(json.dumps(result, cls=CELJSONEncoder))

    def chain(self, lines, *, binding="openai", discovery=False, roles=None, rendered=None, dedicated=True,
              operation="chat/completions"):
        rendered = rendered or self.rendered
        mcp = binding == "mcp-context7"
        path = "/mcp/context7" if mcp else f"/bindings/{binding}/v1/" + (
            "models" if discovery else operation)
        headers = {}
        for name, value in lines:
            name = name.lower()
            if name in headers:
                previous = headers[name]
                headers[name] = [*previous, value] if isinstance(previous, list) else [previous, value]
            else:
                headers[name] = value
        if dedicated:
            headers["x-neurwerk-authorization"] = "Bearer synthetic-platform"
        context = {
            "jwt": {}, "extauthz": {
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
        name = "mcp-context7-policy" if mcp else f"ai-{binding}-" + (
            "model-discovery-processing" if discovery else "api-processing")
        route = resource(rendered, "AgentgatewayPolicy", name)["spec"]["traffic"]
        admitted = self.value(route["authorization"]["policy"]["matchExpressions"][0], context)
        if admitted and not discovery:
            transform = route["transformation"]
            if "conditional" in transform:
                transform = next(branch["policy"] for branch in transform["conditional"]
                                 if self.value(branch["condition"], context))
            expression = transform["request"]["metadata"]["neurwerk_admission"]
            context["metadata"]["neurwerk_admission"] = self.value(expression, context)
            # The extProc contract receives the same frozen source after both sensitive
            # headers are gone; it must not infer source from the remaining headers.
            extproc = route["extProc"]
            if "conditional" in extproc:
                extproc = next(branch["policy"] for branch in extproc["conditional"]
                               if self.value(branch["condition"], context))
            self.assertEqual(self.value(extproc["metadataContext"]["neurwerk.destination_policy"]["request_context"], context),
                             context["metadata"]["neurwerk_admission"])
        return admitted, context

    def test_mode_validation_before_normalization_and_identity_fallback(self):
        for lines in [
            [("X-Neurwerk-Credential-Mode", value)]
            for value in ["", " ", "Global", "NONE", "unknown", "global,none", "global,global", "global\n"]
        ] + [[("X-Neurwerk-Credential-Mode", "global"), ("x-neurwerk-credential-mode", "global")]]:
            with self.subTest(lines=lines):
                self.assertFalse(self.chain(lines)[0])
        self.assertFalse(self.chain([("x-neurwerk-credential-mode", "global"),
                                     ("authorization", "Bearer synthetic")], dedicated=False)[0])
        # Explicit identity extraction cannot fall back even to an otherwise valid OIDC bearer.
        expression = resource(self.rendered, "AgentgatewayPolicy", "ai-admission-api-bindings")[
            "spec"]["traffic"]["jwtAuthentication"]["location"]["expression"]
        self.assertEqual(self.value(expression, {"request": {"headers": {
            "x-neurwerk-credential-mode": "global", "authorization": "Bearer synthetic-oidc",
        }}}), "")

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
            [(name, "synthetic") for name in ["x-api-key"]],
            [("x-goog-api-key", "synthetic")], [("api-key", "synthetic")],
            [("proxy-authorization", "Basic synthetic")],
            [("Cookie", "synthetic=value")], [("cookie", "")],
        ]:
            for binding, mode in [("openai", "global"), ("local", "none")]:
                with self.subTest(extra=extra, mode=mode):
                    self.assertFalse(self.chain([("x-neurwerk-credential-mode", mode), *extra], binding=binding)[0])

    def test_eligibility_permissions_and_override_never_fall_back(self):
        for binding, mode, extra in [
            ("openai", "none", []), ("local", "global", []), ("local", "override", []),
            ("mcp-context7", "global", []), ("mcp-context7", "override", []),
            ("openai", "override", []), ("openai", "override", [("x-api-key", "synthetic")]),
            ("openai", "override", [("authorization", "Bearer one"), ("Authorization", "Bearer two")]),
            ("openai", "override", [("authorization", "Bearer synthetic-provider"), ("Cookie", "synthetic=value")]),
            ("mcp-context7", "none", [("Cookie", "synthetic=value")]),
        ]:
            with self.subTest(binding=binding, mode=mode, extra=extra):
                self.assertFalse(self.chain([("x-neurwerk-credential-mode", mode), *extra], binding=binding)[0])
        self.assertFalse(self.chain([("x-neurwerk-credential-mode", "global")], roles=["llm:invoke"])[0])
        admitted, context = self.chain([("x-neurwerk-credential-mode", "override"),
                                         ("authorization", "Bearer synthetic-provider")])
        self.assertTrue(admitted)
        self.assertEqual(context["request"]["headers"]["authorization"], "Bearer synthetic-provider")
        self.assertEqual(context["metadata"]["neurwerk_admission"]["provider_credential_source"], "override")
        values = copy.deepcopy(self.values)
        values["infraAgentgatewayWrapperSecrets"]["providerCredentials"].pop("example-openai-key")
        self.assertFalse(self.chain([("x-neurwerk-credential-mode", "global")], rendered=render("agentgateway", values))[0])

    def test_discovery_keeps_identity_and_normalized_mode_without_provider_credential(self):
        for binding, mode in [("openai", "global"), ("openai", "override"), ("local", "none")]:
            with self.subTest(binding=binding, mode=mode):
                admitted, context = self.chain([("x-neurwerk-credential-mode", f"\t{mode} ")],
                                                 binding=binding, discovery=True)
                self.assertTrue(admitted)
                self.assertEqual(context["request"]["headers"]["x-neurwerk-credential-mode"], mode)
                self.assertIn("x-neurwerk-authorization", context["request"]["headers"])
                admitted, context = self.chain([
                    ("x-neurwerk-credential-mode", mode), ("authorization", "Bearer synthetic-provider"),
                ], binding=binding, discovery=True)
                self.assertTrue(admitted)
                self.assertNotIn("authorization", context["request"]["headers"])
        self.assertFalse(self.chain([("x-neurwerk-credential-mode", "none")], discovery=True)[0])
        self.assertFalse(self.chain([("x-neurwerk-credential-mode", "global")], discovery=True, roles=["llm:invoke"])[0])
        self.assertFalse(self.chain([("x-neurwerk-credential-mode", "override"), ("api-key", "synthetic")], discovery=True)[0])
        self.assertFalse(self.chain([("x-neurwerk-credential-mode", "override"),
                                    ("Cookie", "synthetic=value")], discovery=True)[0])

    def test_absent_mode_retains_legacy_selection_and_discovery_denial(self):
        for binding, extra, source in [
            ("openai", [], "global"), ("openai", [("authorization", "Bearer synthetic-provider")], "override"),
            ("local", [], "none"), ("mcp-context7", [], "none"),
            ("openai", [("Cookie", "synthetic=value")], "global"),
            ("openai", [("authorization", "Bearer synthetic-provider"), ("Cookie", "synthetic=value")], "override"),
            ("mcp-context7", [("Cookie", "synthetic=value")], "none"),
        ]:
            admitted, context = self.chain(extra, binding=binding)
            self.assertTrue(admitted)
            self.assertEqual(context["metadata"]["neurwerk_admission"]["provider_credential_source"], source)
            self.assertNotIn("cookie", context["request"]["headers"])
        self.assertFalse(self.chain([("authorization", "Bearer synthetic-provider")], discovery=True)[0])

    def test_exact_anthropic_header_and_codex_oauth_are_preserved(self):
        for lines, expected in [
            ([("x-api-key", "synthetic-anthropic")], True),
            ([("authorization", "Bearer synthetic-provider")], False),
        ]:
            admitted, context = self.chain([("x-neurwerk-credential-mode", "override"), *lines],
                                             binding="anthropic", operation="messages")
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
        for selector in [[], [("x-neurwerk-credential-mode", "override")]]:
            admitted, context = self.chain([
                *selector, ("authorization", "Bearer synthetic.oauth.token"),
                ("chatgpt-account-id", "synthetic-account"), ("openai-beta", "responses=experimental"),
            ], rendered=rendered, operation="responses")
            self.assertTrue(admitted)
            self.assertEqual(context["request"]["headers"]["authorization"], "Bearer synthetic.oauth.token")
            self.assertEqual(context["request"]["headers"]["chatgpt-account-id"], "synthetic-account")
        self.assertFalse(self.chain([("x-neurwerk-credential-mode", "global")], rendered=rendered,
                                    operation="responses")[0])

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
            "neurwerk.stripped_credential_headers": ["x-neurwerk-credential-mode", "authorization"],
            "neurwerk.credential_mode": "global", "neurwerk.credential_strip_reason": "explicit_credential_mode",
            "neurwerk.request_id": "synthetic-request",
        })
        self.assertNotIn("never-log-this-value", json.dumps(safe))
        for extra, expected_removed, expected_admitted in [
            ([], [], True),
            ([("authorization", "Bearer never-log-this-value")], ["authorization"], True),
            ([("x-api-key", "never-log-this-value")], ["x-api-key"], False),
        ]:
            with self.subTest(discovery_credentials=extra):
                admitted, discovery_context = self.chain([
                    ("x-neurwerk-credential-mode", " \toverride "), *extra,
                ], discovery=True)
                self.assertEqual(admitted, expected_admitted)
                removed = self.value(fields["neurwerk.stripped_credential_headers"], discovery_context)
                self.assertEqual(removed, expected_removed)
                self.assertEqual(discovery_context["request"]["headers"]["x-neurwerk-credential-mode"], "override")
                self.assertNotIn("x-neurwerk-credential-mode", removed)
                for name in removed:
                    self.assertNotIn(name, discovery_context["request"]["headers"])
                self.assertNotIn("never-log-this-value", json.dumps(removed))
        context["metadata"] = {}
        for key in ["neurwerk.stripped_credential_headers", "neurwerk.credential_mode", "neurwerk.credential_strip_reason"]:
            self.assertIsNone(self.value(fields[key], context))
