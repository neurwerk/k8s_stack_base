"""Opt-in v5 routing, identity isolation and effective catalog contracts."""

import copy
import json
import re
import unittest

import yaml
from celpy import CELEvalError, Environment, celtypes, json_to_cel

from helm import documents, render, resource


def binding_values():
    return {
        "guardrails": {"llmPolicyEngine": {"models": []}},
        "authKeycloak": {
            "agentgatewayClientRoles": [
                "llm:invoke",
                "model:remote/example/model:invoke",
                "model:example-chat:invoke",
                "model:example-claude:invoke",
                "model:example-gemini:invoke",
                "model:example-router:invoke",
                "model:local-chat:invoke",
                "provider:openai:company-use",
                "provider:anthropic:company-use",
                "provider:google:company-use",
                "provider:openrouter:company-use",
            ]
        },
        "aiGateway": {
            "enabled": True,
            "bindings": [
                {
                    "id": "openai",
                    "enabled": True,
                    "providerId": "openai",
                    "upstreamURL": "https://provider.example.test/v1",
                    "credentialSources": ["caller_supplied", "company"],
                    "authSecret": "example-openai-key",
                    "inspect": True,
                    "profiles": ["openai-chat-completions-v1", "openai-responses-v1"],
                    "models": [{"name": "example-chat", "upstreamModel": "provider-chat"}],
                },
                {
                    "id": "anthropic",
                    "enabled": True,
                    "providerId": "anthropic",
                    "provider": "Anthropic",
                    "upstreamURL": "https://anthropic.example.test/v1",
                    "credentialSources": ["caller_supplied", "company"],
                    "authSecret": "example-anthropic-key",
                    "inspect": True,
                    "profiles": ["anthropic-messages-v1"],
                    "models": [{"name": "example-claude", "upstreamModel": "provider-claude"}],
                },
                {
                    "id": "google",
                    "enabled": True,
                    "providerId": "google",
                    "provider": "Gemini",
                    "upstreamURL": "https://google.example.test",
                    "credentialSources": ["caller_supplied", "company"],
                    "authSecret": "example-google-key",
                    "api": False,
                    "inspect": True,
                    "profiles": ["google-generate-content-v1"],
                    "models": [{"name": "example-gemini", "upstreamModel": "provider-gemini"}],
                },
                {
                    "id": "local",
                    "enabled": True,
                    "upstreamURL": "http://local-model.example.svc.cluster.local:8080/v1",
                    "local": True,
                    "credentialSources": ["none"],
                    "profiles": ["openai-chat-completions-v1"],
                    "models": [{"name": "local-chat", "upstreamModel": "local-provider-chat"}],
                },
                {
                    "id": "openrouter",
                    "enabled": True,
                    "providerId": "openrouter",
                    "provider": "Openrouter",
                    "upstreamURL": "https://router.example.test/api/v1",
                    "credentialSources": ["caller_supplied", "company"],
                    "authSecret": "example-openrouter-key",
                    "inspect": True,
                    "profiles": ["openai-chat-completions-v1", "openai-responses-v1"],
                    "models": [{"name": "example-router", "upstreamModel": "provider/router-chat"}],
                },
            ],
            "proxy": {
                "enabled": True,
                "hostname": "proxy.example.test",
                "tlsSecretName": "example-proxy-tls",
                "signerSecretName": "example-inspection-signer",
                "ingressPeers": [
                    {
                        "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                        "podSelector": {"matchLabels": {"app.kubernetes.io/name": "traefik"}},
                    }
                ],
            },
        },
    }


class ApprovedOidcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        values = binding_values()
        values["mcp"] = {"enabled": True, "contextforgeRoutesEnabled": ["context7"],
                         "catalog": {"presets": {"context7": {"enabled": True}}}}
        values["authKeycloak"]["agentgatewayClientRoles"].append("mcp:context7:invoke")
        values["aiGateway"]["approvedOidcClients"] = [
            {"clientId": "addon-one", "bindings": ["openai", "mcp-context7"]},
            {"clientId": "addon-two", "bindings": ["local"]},
        ]
        cls.values = values
        cls.rendered = render("agentgateway", values)
        traffic = resource(cls.rendered, "AgentgatewayPolicy", "ai-admission-api-bindings")["spec"]["traffic"]
        cls.expression = traffic["authorization"]["policy"]["matchExpressions"][0]
        cls.roles = ["llm:invoke", "model:example-chat:invoke", "model:local-chat:invoke",
                     "provider:openai:company-use", "mcp:context7:invoke"]

    def evaluate(self, expression, context):
        env = Environment()
        # AgentGateway's raw() preserves repeated header values; model that
        # extension while evaluating the actual rendered CEL without rewriting it.
        program = env.program(env.compile(expression), functions={
            "raw": lambda headers: celtypes.MapType({
                key: celtypes.ListType([value]) for key, value in headers.items()
            }),
        })
        try:
            return bool(program.evaluate({key: json_to_cel(value) for key, value in context.items()}))
        except CELEvalError:
            return False

    def admitted(self, client="addon-one", path="/bindings/openai/v1/chat/completions",
                 audience="agentgateway", roles=None, **claims):
        jwt = {"sub": "test-user", "azp": client, "aud": audience,
               "resource_access": {"agentgateway": {"roles": self.roles if roles is None else roles}}}
        jwt.update(claims)
        return self.evaluate(self.expression, {
            "jwt": jwt, "extauthz": {},
            "request": {"path": path, "method": "POST", "headers": {"authorization": "Bearer synthetic"}},
        })

    def test_two_addons_are_scoped_to_exact_bindings(self):
        self.assertTrue(self.admitted())
        self.assertTrue(self.admitted(audience=["other", "agentgateway"]))
        self.assertTrue(self.admitted(client="addon-two", path="/bindings/local/v1/chat/completions"))
        self.assertTrue(self.admitted(path="/bindings/mcp-context7/mcp"))
        for client, path in [
            ("addon-one", "/bindings/local/v1/chat/completions"),
            ("addon-two", "/bindings/openai/v1/chat/completions"),
            ("addon-two", "/bindings/mcp-context7/mcp"),
            ("addon-one", "/bindings/openai/v1/chat/completions/extra"),
            ("addon-one", "/v1/chat/completions"),
        ]:
            with self.subTest(client=client, path=path):
                self.assertFalse(self.admitted(client=client, path=path))

    def test_client_audience_and_permissions_fail_closed(self):
        for claims in [{"client": "unapproved"}, {"client": ""}, {"azp": None},
                       {"audience": "addon-browser"}, {"audience": ["addon-browser"]},
                       {"audience": None}, {"audience": 7}, {"sub": ""},
                       {"roles": []}, {"roles": "llm:invoke"},
                       {"roles": [r for r in self.roles if r != "llm:invoke"]},
                       {"roles": [r for r in self.roles if r != "provider:openai:company-use"]},
                       {"resource_access": {}},
                       {"path": "/bindings/mcp-context7/mcp", "roles": ["llm:invoke"]}]:
            with self.subTest(claims=claims):
                self.assertFalse(self.admitted(**claims))

    def test_builtin_and_managed_key_access_remains_independent(self):
        self.assertTrue(self.admitted(client="agentgateway"))
        self.assertFalse(self.admitted(client="addon-browser"))
        self.assertTrue(self.admitted(client="studio", path="/bindings/mcp-context7/mcp",
                                      realm_access={"roles": ["studio-user"]}))
        self.assertFalse(self.admitted(client="studio", realm_access={"roles": ["studio-user"]}))
        self.assertFalse(self.admitted(client="studio", path="/bindings/mcp-context7/mcp"))
        self.assertTrue(self.evaluate(self.expression, {
            "jwt": {}, "extauthz": {"contract_version": 1, "principal_id": "service-user",
                "credential_id": "key-id", "credential_kind": "managed", "permissions": self.roles},
            "request": {"path": "/bindings/openai/v1/chat/completions", "method": "POST",
                        "headers": {"x-neurwerk-authorization": "Bearer synthetic"}},
        }))

    def test_invalid_approvals_are_rejected_at_render(self):
        for approval in [
            {"clientId": "studio", "bindings": ["openai"]},
            {"clientId": "agentgateway", "bindings": ["openai"]},
            {"clientId": "addon-one", "bindings": ["unknown"]},
            {"clientId": "addon-one", "bindings": ["google"]},
            {"clientId": "addon-one", "bindings": ["mcp-unknown"]},
            {"clientId": "addon-one", "bindings": []},
            {"clientId": "addon-one", "bindings": ["openai"], "permissions": ["llm:invoke"]},
        ]:
            with self.subTest(approval=approval):
                values = binding_values()
                values["aiGateway"]["approvedOidcClients"] = [approval]
                self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)

    def test_addon_access_requires_explicit_binding_approval(self):
        for mode in ["omitted", "null", "narrow", "revoked"]:
            with self.subTest(mode=mode):
                values = binding_values()
                values["mcp"] = self.values["mcp"]
                values["authKeycloak"]["agentgatewayClientRoles"].append("mcp:context7:invoke")
                if mode == "null":
                    values["aiGateway"]["approvedOidcClients"] = None
                elif mode == "narrow":
                    values["aiGateway"]["approvedOidcClients"] = [
                        {"clientId": "addon-browser", "bindings": ["openai"]},
                    ]
                elif mode == "revoked":
                    values["aiGateway"]["approvedOidcClients"] = []
                rendered = render("agentgateway", values)
                traffic = resource(rendered, "AgentgatewayPolicy", "ai-admission-api-bindings")["spec"]["traffic"]
                self.expression = traffic["authorization"]["policy"]["matchExpressions"][0]
                for audience in ["agentgateway", ["agentgateway"], "addon-browser", ["addon-browser"]]:
                    self.assertEqual(self.admitted(client="addon-browser", audience=audience),
                                     mode == "narrow" and "agentgateway" in audience)
                for path in ["/bindings/local/v1/chat/completions", "/bindings/mcp-context7/mcp"]:
                    self.assertFalse(self.admitted(client="addon-browser", path=path))
                self.assertFalse(self.admitted(client="addon-browser", path="/v1/chat/completions"))
                self.assertFalse(self.admitted(client="addon-browser", path="/bindings/openai/v1/chat/completions/extra"))
                self.assertFalse(self.admitted(client="addon-one"))
                self.assertTrue(self.admitted(client="agentgateway"))
                self.assertTrue(self.admitted(client="studio", path="/bindings/mcp-context7/mcp",
                                              realm_access={"roles": ["studio-user"]}))
                self.assertFalse(self.admitted(client="studio", path="/bindings/mcp-context7/mcp"))
                self.assertFalse(self.admitted(client="studio", realm_access={"roles": ["studio-user"]}))

    def test_approval_does_not_replace_destination_permissions(self):
        model = next(doc for doc in documents(self.rendered)
                     if doc["kind"] == "AgentgatewayModel"
                     and doc["spec"].get("policies", {}).get("auth", {}).get("secretRef", {}).get("name") == "example-openai-key")
        expression = model["spec"]["policies"]["authorization"]["policy"]["matchExpressions"][0]
        revision = re.search(r'policy_revision == "([a-f0-9]+)"', expression).group(1)
        for roles, expected in [(self.roles, True),
                                ([r for r in self.roles if r != "model:example-chat:invoke"], False),
                                ([r for r in self.roles if r != "provider:openai:company-use"], False)]:
            admission = {"binding_id": "openai", "provider_credential_source": "company",
                         "policy_revision": revision, "permissions": roles}
            context = {**admission, "resolved_model": "provider-chat", "destination_id": "example-chat",
                       "dispatch_destination_id": "example-chat", "dispatch_binding_id": "openai",
                       "requested_model": "example-chat", "content_tracing_enabled": False,
                       "pii_policy": {"enabled": True}}
            with self.subTest(roles=roles):
                self.assertEqual(self.evaluate(expression, {
                    "metadata": {"neurwerk_admission": admission},
                    "extproc": {"neurwerk.request_context": context},
                }), expected)
        mcp = resource(self.rendered, "AgentgatewayPolicy", "mcp-context7-policy")
        expression = mcp["spec"]["traffic"]["authorization"]["policy"]["matchExpressions"][0]
        for binding, roles, expected in [("mcp-context7", self.roles, True),
                                         ("mcp-context7", ["llm:invoke"], False),
                                         ("openai", self.roles, False)]:
            self.assertEqual(self.evaluate(expression, {"metadata": {"neurwerk_admission": {
                "binding_id": binding, "destination_kind": "mcp", "permissions": roles,
            }}}), expected)


class EndpointRoutingTests(unittest.TestCase):
    evaluate = ApprovedOidcTests.evaluate

    @classmethod
    def setUpClass(cls):
        cls.values = binding_values()
        binding = cls.values["aiGateway"]["bindings"][0]
        binding.update(apiPath="/openai/codex", upstreamURL="https://chatgpt.com/backend-api/codex",
                       credentialSources=["caller_supplied"], profiles=["openai-codex-responses-v1"])
        binding.pop("authSecret")
        cls.rendered = render("agentgateway", cls.values)

    def test_exact_custom_path_admission_and_trusted_context(self):
        for listener, path, prefix in [
            ("api-bindings", "/openai/codex/v1/responses", "/openai/codex"),
            ("inspect-openai", "/backend-api/codex/responses", None),
        ]:
            traffic = resource(self.rendered, "AgentgatewayPolicy", f"ai-admission-{listener}")["spec"]["traffic"]
            context = {"jwt": {}, "extauthz": {
                "contract_version": 1, "request_id": "test-request", "principal_id": "test-user",
                "credential_id": "test-key", "credential_kind": "personal", "permissions": ["llm:invoke"],
            }, "request": {"path": path, "host": "chatgpt.com", "method": "POST", "headers": {
                "authorization": "Bearer provider-synthetic", "x-neurwerk-authorization": "Bearer platform-synthetic",
            }}}
            expression = traffic["authorization"]["policy"]["matchExpressions"][0]
            self.assertTrue(self.evaluate(expression, context))
            selected = [entry for entry in traffic["transformation"]["conditional"]
                        if self.evaluate(entry["condition"], context)]
            self.assertEqual(len(selected), 1)
            env = Environment()
            metadata = selected[0]["policy"]["request"]["metadata"]["neurwerk_admission"]
            admitted = env.program(env.compile(metadata)).evaluate(
                {key: json_to_cel(value) for key, value in context.items()})
            self.assertEqual(admitted["binding_id"], "openai")
            self.assertEqual(admitted["protocol_profile"], "openai-codex-responses-v1")
            if prefix:
                self.assertEqual(admitted["api_path_prefix"], prefix)
                self.assertEqual(path.removeprefix(str(admitted["api_path_prefix"])), "/v1/responses")
            else:
                self.assertNotIn("api_path_prefix", admitted)
            for wrong in ["/v1/responses", "/bindings/openai/v1/responses", path + "/extra",
                          path.replace("/responses", "//responses"), "/openai/api/v1/responses"]:
                with self.subTest(listener=listener, path=wrong):
                    context["request"]["path"] = wrong
                    self.assertFalse(self.evaluate(expression, context))

    def test_codex_api_prefix_survives_until_native_routing(self):
        for mode, incoming, expected in [
            ("api", "/openai/codex/v1/responses", "/openai/codex/v1/responses"),
            ("proxy", "/backend-api/codex/responses", "/v1/responses"),
        ]:
            policy = resource(self.rendered, "AgentgatewayPolicy", f"ai-openai-{mode}-processing")["spec"]["traffic"]
            self.assertEqual(policy["phase"], "PostRouting")
            path = incoming
            for header in policy.get("transformation", {}).get("request", {}).get("set", []):
                if header["name"] == ":path":
                    env = Environment()
                    path = str(env.program(env.compile(header["value"])).evaluate({}))
            self.assertEqual(path, expected)
            route = resource(self.rendered, "HTTPRoute", f"ai-openai-{mode}")["spec"]["rules"][0]
            prefix = route["matches"][0]["path"]["value"].rstrip("/")
            self.assertEqual(path.removeprefix(prefix), "/v1/responses")

    def test_malformed_and_overlapping_prefixes_fail_render(self):
        for prefix in ["", "/openai/api/", "/openai/api/v1", "/openai/../api",
                       "/openai//api", "/bindings/mcp-example", "/openai/api?x=1", 3]:
            with self.subTest(prefix=prefix):
                values = copy.deepcopy(self.values)
                values["aiGateway"]["bindings"][0]["apiPath"] = prefix
                self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)
        for prefix in ["/openai/codex", "/openai", "/openai/codex/nested"]:
            with self.subTest(overlap=prefix):
                values = copy.deepcopy(self.values)
                values["aiGateway"]["bindings"][1]["apiPath"] = prefix
                self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)

    def test_external_route_reaches_custom_legacy_and_mcp_mounts(self):
        for legacy, mcp in [(False, False), (True, False), (False, True), (True, True)]:
            with self.subTest(legacy=legacy, mcp=mcp):
                values = copy.deepcopy(self.values)
                for binding in values["aiGateway"]["bindings"]:
                    if binding.get("api", True) and binding["id"] != "openai" and not legacy:
                        binding["apiPath"] = f"/{binding['id']}/api"
                if mcp:
                    values["mcp"] = {"enabled": True, "contextforgeRoutesEnabled": ["context7"],
                                     "catalog": {"presets": {"context7": {"enabled": True}}}}
                    values["authKeycloak"]["agentgatewayClientRoles"].append("mcp:context7:invoke")
                rendered = render("agentgateway", values)
                external = resource(rendered, "HTTPRoute", "infra-agentgateway-external-httproute")["spec"]
                matches = [match["path"] for rule in external["rules"] for match in rule["matches"]]
                prefixes = [match["value"] for match in matches]
                self.assertTrue(all(match["type"] == "PathPrefix" for match in matches))
                self.assertEqual(len(prefixes), len(set(prefixes)))
                self.assertNotIn("/", prefixes)
                self.assertEqual("/bindings" in prefixes, legacy or mcp)
                for left in prefixes:
                    self.assertFalse(any(right != left and right.startswith(left + "/") for right in prefixes))
                for binding in values["aiGateway"]["bindings"]:
                    if not binding.get("api", True):
                        continue
                    internal = resource(rendered, "HTTPRoute", f"ai-{binding['id']}-api")["spec"]
                    prefix = internal["rules"][0]["matches"][0]["path"]["value"]
                    operation = {"openai-codex-responses-v1": "/v1/responses",
                                 "anthropic-messages-v1": "/v1/messages"}.get(
                                     binding["profiles"][0], "/v1/chat/completions")
                    path = prefix + operation
                    self.assertEqual(sum(path.startswith(edge + "/") for edge in prefixes), 1)
                if mcp:
                    self.assertEqual(sum("/bindings/mcp-context7/mcp".startswith(edge + "/")
                                         for edge in prefixes), 1)

    def test_included_billing_requires_fixed_caller_supplied_codex(self):
        values = copy.deepcopy(self.values)
        values["aiGateway"]["bindings"][0]["billingMode"] = "included"
        render("agentgateway", values)
        for override in [
            {"billingMode": "free"}, {"billingMode": None},
            {"profiles": ["openai-responses-v1"]},
            {"upstreamURL": "https://chatgpt.com/backend-api/other"},
            {"credentialSources": ["company"], "authSecret": "example-openai-key"},
            {"credentialSources": ["caller_supplied", "company"], "authSecret": "example-openai-key"},
        ]:
            with self.subTest(override=override):
                invalid = copy.deepcopy(values)
                invalid["aiGateway"]["bindings"][0].update(override)
                self.assertNotEqual(render("agentgateway", invalid, check=False).returncode, 0)


class InspectedGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enabled = render("agentgateway", binding_values())

    def test_retired_listener_and_unbound_routes_are_absent(self):
        result = render("agentgateway")
        listeners = resource(result, "Gateway", "infra-agentgateway-gateway")["spec"]["listeners"]
        self.assertEqual([entry["name"] for entry in listeners], ["api-bindings"])
        self.assertNotIn("infra-agentgateway-auth-ag-policy", result.stdout)
        route = resource(result, "HTTPRoute", "infra-agentgateway-external-httproute")
        self.assertEqual([rule["matches"][0]["path"]["value"] for rule in route["spec"]["rules"]], ["/bindings"])
        self.assertNotEqual(render("agentgateway", {"aiGateway": {"enabled": False}}, check=False).returncode, 0)

    def test_identity_is_separate_from_provider_credentials(self):
        values = binding_values()
        values["mcp"] = {"enabled": True, "contextforgeRoutesEnabled": ["context7"],
                         "catalog": {"presets": {"context7": {"enabled": True}}}}
        values["authKeycloak"]["agentgatewayClientRoles"].append("mcp:context7:invoke")
        rendered = render("agentgateway", values)
        for listener, header, source, path in [
            ("api-bindings", "x-neurwerk-authorization", "request.headers", "/validate/neurwerk"),
            ("inspect-openai", "proxy-authorization", "source.connectHeaders", "/validate/proxy"),
            ("inspect-anthropic", "proxy-authorization", "source.connectHeaders", "/validate/proxy"),
            ("inspect-google", "proxy-authorization", "source.connectHeaders", "/validate/proxy"),
            ("inspect-openrouter", "proxy-authorization", "source.connectHeaders", "/validate/proxy"),
        ]:
            traffic = resource(rendered, "AgentgatewayPolicy", f"ai-admission-{listener}")["spec"]["traffic"]
            if listener == "api-bindings":
                self.assertIn("x-neurwerk-authorization", traffic["jwtAuthentication"]["location"]["expression"])
                self.assertIn('size(request.headers.raw()["authorization"]) == 1', traffic["jwtAuthentication"]["location"]["expression"])
            else:
                self.assertNotIn("jwtAuthentication", traffic)
            auth = traffic["extAuth"]["conditional"][0]["policy"]["http"]
            self.assertEqual(auth["path"], json.dumps(path))
            self.assertEqual(auth["allowedRequestHeaders"], ["content-type"])
            self.assertEqual(set(auth["addRequestHeaders"]), {header, "x-request-id"})
            self.assertIn(f'size({source}.raw()["{header}"]) == 1', auth["addRequestHeaders"][header])
            for expression in traffic["authorization"]["policy"]["matchExpressions"]:
                self.assertNotIn("source.connectHeaders", expression)
            for conditional in traffic["transformation"]["conditional"]:
                for direction in ("request", "response"):
                    self.assertEqual(conditional["policy"][direction]["set"], [
                        {"name": "x-request-id", "value": "metadata.neurwerk_admission.request_id"},
                    ])
                request = conditional["policy"]["request"]
                context = request["metadata"]["neurwerk_admission"]
                self.assertEqual(context.count("uuid()"), 1)
                self.assertNotIn("request.headers[", context)
                self.assertIn("x-neurwerk-authorization", request["remove"])
                self.assertIn("proxy-authorization", request["remove"])
                if '&& (has(jwt.sub)' in conditional["condition"]:
                    self.assertIn("authorization", request["remove"])
                else:
                    self.assertEqual("authorization" in request["remove"], not any(
                        f'"binding_id": "{name}"' in context for name in ["openai", "openrouter"]))
        self.assertNotIn("infra-agentgateway-auth-ag-policy", self.enabled.stdout)

    def test_fixed_upstreams_models_and_no_company_fallback(self):
        models = [m for m in documents(self.enabled) if m["kind"] == "AgentgatewayModel" and m["metadata"]["name"].startswith("ai-")]
        self.assertEqual(len(models), 15)
        bindings = {item["id"]: item for item in binding_values()["aiGateway"]["bindings"]}
        for model in models:
            spec = model["spec"]
            binding = model["metadata"]["name"].split("-")[1]
            if spec["visibility"] == "Public":
                self.assertNotIn("provider", spec)
                self.assertNotIn("policies", spec)
                targets = spec["virtualModel"]["conditional"]["targets"]
                self.assertEqual(len(targets), len(bindings[binding]["credentialSources"]))
                self.assertTrue(all("when" in target for target in targets))
                self.assertNotIn("failover", spec["virtualModel"])
                continue
            self.assertEqual("auth" in spec["policies"], model["metadata"]["name"].endswith("-company"))
            self.assertNotIn("retry", spec["policies"])
            self.assertIn('extproc["neurwerk.request_context"].resolved_model', spec["policies"]["authorization"]["policy"]["matchExpressions"][0])
            self.assertIn("metadata.neurwerk_admission.all", spec["policies"]["authorization"]["policy"]["matchExpressions"][0])
            expected = bindings[binding]["models"][0]
            expression = spec["policies"]["authorization"]["policy"]["matchExpressions"][0]
            self.assertIn(f'.destination_id == "{expected["name"]}"', expression)
            self.assertIn(f'.resolved_model == "{expected["upstreamModel"]}"', expression)
            self.assertIn(".pii_policy.enabled == true", expression)
            self.assertEqual(spec["policies"]["transformations"][0]["field"], "model")
            for parent in spec["parentRefs"]:
                self.assertEqual(parent["kind"], "HTTPRoute")
        for entry in ["openai-api", "openai-proxy", "anthropic-api", "anthropic-proxy", "google-proxy", "local-api"]:
            policy = resource(self.enabled, "AgentgatewayPolicy", f"ai-{entry}-processing")["spec"]["traffic"]
            self.assertEqual(policy["phase"], "PostRouting")
            self.assertEqual(policy["retry"], {"attempts": 1, "precondition": "false", "codes": []})
            processor = policy["extProc"]
            self.assertEqual(processor["failureMode"], "FailClosed")
            self.assertEqual(processor["processingOptions"]["requestBodyMode"], "Buffered")
            self.assertFalse(processor["processingOptions"]["allowModeOverride"])
            metadata = processor["metadataContext"]["neurwerk.destination_policy"]
            self.assertEqual(set(metadata), {"contract_version", "request_context", "destination_policy", "model_bindings", "reroute_targets"})
            self.assertEqual(metadata["contract_version"], "5")
            self.assertEqual(metadata["request_context"], "metadata.neurwerk_admission")
            for model in json.loads(metadata["model_bindings"]).values():
                self.assertEqual(set(model), {"upstream_model", "required_permissions", "content_tracing_enabled"})
            self.assertNotIn("uuid()", json.dumps(metadata))

    def test_exact_host_inspection_and_private_exposure(self):
        gateway = resource(self.enabled, "Gateway", "infra-agentgateway-gateway")
        self.assertEqual(gateway["metadata"]["annotations"]["agentgateway.dev/internal-ports"], "443")
        listeners = {entry["name"]: entry for entry in gateway["spec"]["listeners"]}
        for name, host in [("openai", "provider.example.test"), ("anthropic", "anthropic.example.test"), ("google", "google.example.test")]:
            listener = listeners[f"inspect-{name}"]
            self.assertEqual(listener["hostname"], host)
            self.assertEqual(listener["port"], 443)
            self.assertEqual(listener["tls"]["options"]["agentgateway.dev/tls-certificate-source"], "DYNAMIC_CA")
            policy = resource(self.enabled, "AgentgatewayPolicy", f"ai-admission-inspect-{name}")["spec"]
            self.assertIn(f'request.host in ["{host}","{host}:443"]', policy["traffic"]["authorization"]["policy"]["matchExpressions"][0])
        service = resource(self.enabled, "Service", "infra-agentgateway-forward-proxy")["spec"]
        self.assertEqual(service["type"], "ClusterIP")
        self.assertEqual([port["port"] for port in service["ports"]], [8443])
        network = resource(self.enabled, "NetworkPolicy", "infra-agentgateway-data-plane-network-policy")["spec"]
        ports = [port["port"] for rule in network["ingress"] for port in rule["ports"]]
        self.assertIn(8443, ports)
        self.assertNotIn(443, ports)

    def test_single_catalog_and_metadata_only_telemetry(self):
        catalog_map = resource(self.enabled, "ConfigMap", "agentgateway-catalog-config-map")
        self.assertEqual(catalog_map["metadata"]["namespace"], "frontend-studio")
        catalog = json.loads(catalog_map["data"]["catalog.json"])
        self.assertEqual(catalog["schema_version"], 1)
        endpoints = {entry["id"]: entry for entry in catalog["endpoints"]}
        self.assertEqual(endpoints["api-openai"]["url"], "https://agentgateway.lint.example/bindings/openai/v1")
        self.assertEqual(endpoints["api-openai"]["caller_auth_modes"], ["neurwerk_bearer", "oidc_bearer"])
        destination = next(entry for entry in catalog["destinations"] if entry["id"] == "example-chat")
        self.assertEqual(set(destination["endpoint_ids"]), {"api-openai", "forward-proxy"})
        modes = {entry["source"]: entry for entry in destination["credential_modes"]}
        self.assertEqual(modes["company"]["required_permissions"], ["provider:openai:company-use"])
        self.assertEqual(modes["caller_supplied"]["required_permissions"], [])
        self.assertNotIn("agentgateway-status-config-map", self.enabled.stdout)
        self.assertNotIn("agentgateway-inspection-trust-config-map", self.enabled.stdout)
        self.assertTrue(all(operation["support"] == "unknown" for profile in catalog["profiles"] for operation in profile["operations"]))
        params = resource(self.enabled, "AgentgatewayParameters")["spec"]["rawConfig"]
        db = params["frontendPolicies"]["accessLog"]["database"]
        self.assertEqual(db["llm"], "metadata")
        tracing = params["config"]["tracing"]["fields"]["add"]
        for field, value in db["add"].items():
            self.assertEqual(tracing[field], value)
            self.assertNotIn("permissions", value)
            self.assertNotIn("headers", value)
        self.assertIn('"unknown"', db["add"]["neurwerk.billing_classification"])
        self.assertIn("content_tracing_enabled", tracing["gen_ai.prompt"])
        self.assertNotIn("response.body", json.dumps(tracing))

    def test_langfuse_top_level_metadata_matches_native_accounting_allowlist(self):
        params = resource(self.enabled, "AgentgatewayParameters")["spec"]["rawConfig"]
        stdout = params["frontendPolicies"]["accessLog"]["add"]
        database = params["frontendPolicies"]["accessLog"]["database"]["add"]
        tracing = params["config"]["tracing"]["fields"]["add"]
        result = render("langfuse", namespace="monitor-langfuse")
        collector = yaml.safe_load(resource(result, "ConfigMap", "monitor-langfuse-otel-collector-config-map")["data"]["config.yaml"])
        self.assertEqual(collector["service"]["pipelines"]["traces"]["processors"], ["transform/langfuse_neurwerk", "batch"])
        transform = collector["processors"]["transform/langfuse_neurwerk"]
        self.assertEqual(transform["error_mode"], "propagate")
        group, = transform["trace_statements"]
        self.assertEqual(group["context"], "span")
        gateway_name = resource(self.enabled, "Gateway", "infra-agentgateway-gateway")["metadata"]["name"]
        self.assertEqual(group["conditions"], [f'resource.attributes["service.name"] == "{gateway_name}" and attributes["neurwerk.request_id"] != nil'])
        statements = group["statements"]
        fields = {
            "request_id", "entry_mode", "destination_kind", "destination_id", "provider_id", "binding_id",
            "protocol_profile", "operation_id", "provider_credential_source", "policy_revision",
            "requested_model", "resolved_model", "billing_classification", "token_usage_known", "cost_known",
            "dispatch_binding_id", "dispatch_destination_id", "request_text_inspection",
            "target_credential_mode", "dispatch_attempted", "request_outcome",
        }
        prefix = "langfuse.observation.metadata.neurwerk."
        targets = set(re.findall(r'set\(attributes\["([^\"]+)"\]', "\n".join(statements)))
        self.assertEqual(targets, {prefix + field for field in fields})
        self.assertEqual(len(statements), 45)
        for field in fields:
            key = f"neurwerk.{field}"
            self.assertEqual(database[key], stdout[key])
            self.assertEqual(database[key], tracing[key])
            target = prefix + field
            self.assertIn(f'delete_key(attributes, "{target}")', statements)
            setters = [statement for statement in statements if statement.startswith(f'set(attributes["{target}"]')]
            if field in {"token_usage_known", "cost_known", "dispatch_attempted"}:
                self.assertEqual(setters, [f'set(attributes["{target}"], {value}) where attributes["{key}"] == {value} or attributes["{key}"] == "{value}"'
                                           for value in ["true", "false"]])
            else:
                self.assertEqual(setters, [f'set(attributes["{target}"], attributes["{key}"]) where attributes["{key}"] != nil'])
            self.assertLess(statements.index(f'delete_key(attributes, "{target}")'), statements.index(setters[0]))
        for sink in (database, stdout, tracing):
            self.assertFalse(any(key.startswith(prefix) for key in sink))
        self.assertIn('.destination_id, null)', database["neurwerk.destination_id"])
        self.assertIn('.resolved_model, null)', database["neurwerk.resolved_model"])
        self.assertNotIn("llm.requestModel", database["neurwerk.destination_id"])
        self.assertNotIn("credential_id", " ".join(statements))
        self.assertNotIn("credential_kind", " ".join(statements))

    def test_collector_rolls_when_its_configuration_changes(self):
        def checksum(values):
            result = render("langfuse", values, namespace="monitor-langfuse")
            pod = resource(result, "Deployment", "monitor-langfuse-otel-collector-deployment")["spec"]["template"]
            return pod["metadata"]["annotations"]["checksum/config"]

        original = checksum({})
        self.assertRegex(original, r"^[a-f0-9]{64}$")
        self.assertNotEqual(original, checksum({"monitorLangfuseWrapper": {"otelCollector": {"ports": {"http": 5321}}}}))

    def test_api_only_catalog_and_disabled_binding(self):
        values = binding_values()
        values["aiGateway"]["proxy"]["enabled"] = False
        for binding in values["aiGateway"]["bindings"]:
            binding["inspect"] = False
        values["aiGateway"]["bindings"][1]["enabled"] = False
        values["aiGateway"]["bindings"][2]["enabled"] = False
        result = render("agentgateway", values)
        catalog = json.loads(resource(result, "ConfigMap", "agentgateway-catalog-config-map")["data"]["catalog.json"])
        self.assertNotIn("forward-proxy", {entry["id"] for entry in catalog["endpoints"]})
        self.assertNotIn("api-anthropic", {entry["id"] for entry in catalog["endpoints"]})
        self.assertNotIn("ai-anthropic-api", result.stdout)

    def test_public_binding_selects_source_without_reading_or_falling_back_on_token(self):
        traffic = resource(self.enabled, "AgentgatewayPolicy", "ai-admission-api-bindings")["spec"]["traffic"]
        context = traffic["transformation"]["conditional"][0]["policy"]["request"]["metadata"]["neurwerk_admission"]
        self.assertIn('"authorization" in request.headers ? "caller_supplied" : "company"', context)
        self.assertNotIn("response", context)
        gate = traffic["authorization"]["policy"]["matchExpressions"][0]
        self.assertIn('"authorization" in request.headers ? true : ("provider:openai:company-use" in (has(jwt.sub) ? jwt.resource_access.agentgateway.roles : extauthz.permissions))', gate)
        self.assertIn('h == "authorization" || !(h in request.headers)', gate)

    def test_canonical_catalog_contains_only_bound_models(self):
        values = binding_values()
        values["guardrails"] = {"llmPolicyEngine": {"models": [{
            "name": "example-chat", "model": "provider-chat", "provider": "OpenAI", "baseURL": "https://provider.example.test/v1",
        }]}}
        result = render("agentgateway", values)
        catalog = json.loads(resource(result, "ConfigMap", "agentgateway-catalog-config-map")["data"]["catalog.json"])
        self.assertEqual(sum(entry["id"] == "example-chat" for entry in catalog["destinations"]), 1)
        self.assertNotIn("legacy-api", json.dumps(catalog))

    def test_google_only_admits_exact_model_operation_paths(self):
        traffic = resource(self.enabled, "AgentgatewayPolicy", "ai-admission-inspect-google")["spec"]["traffic"]
        variants = traffic["transformation"]["conditional"]
        self.assertEqual(len(variants), 2)
        for variant, operation in zip(variants, ["generateContent", "streamGenerateContent"]):
            self.assertIn(f'"/v1beta/models/example-gemini:{operation}"', variant["condition"])
            self.assertIn(f'"/v1beta/models/provider-gemini:{operation}"', variant["condition"])
            self.assertNotIn("startsWith", variant["condition"])
            self.assertIn(f'"operation_id": "{operation}"', variant["policy"]["request"]["metadata"]["neurwerk_admission"])
        models = [m["spec"] for m in documents(self.enabled) if m["kind"] == "AgentgatewayModel"
                  and m["metadata"]["name"].startswith("ai-google-") and m["spec"]["visibility"] == "Public"]
        self.assertEqual({model["match"]["model"] for model in models}, {"example-gemini", "provider-gemini"})
        self.assertEqual(models[0]["virtualModel"], models[1]["virtualModel"])

    def test_revision_tracks_effective_policy_and_not_provider_secret_contents(self):
        def catalog(values):
            result = render("agentgateway", values)
            return json.loads(resource(result, "ConfigMap", "agentgateway-catalog-config-map")["data"]["catalog.json"])

        values = binding_values()
        original = catalog(values)
        values["aiGateway"]["bindings"][0]["models"][0]["contentTracingEnabled"] = True
        self.assertNotEqual(original["policy_revision"], catalog(values)["policy_revision"])
        values = binding_values()
        values["infraAgentgatewayWrapperSecrets"] = {"databasePassword": "different-synthetic-password"}
        self.assertEqual(original, catalog(values))
        values = binding_values()
        values["aiGateway"]["bindings"].append({"id": "future", "enabled": False})
        self.assertEqual(original, catalog(values))

    def test_unsafe_or_ambiguous_bindings_fail_render(self):
        mutations = [
            ("personal-only secret", lambda v: v["aiGateway"]["bindings"][0].update(credentialSources=["caller_supplied"])),
            ("missing provider role", lambda v: v["authKeycloak"]["agentgatewayClientRoles"].remove("provider:openai:company-use")),
            ("duplicate inspection host", lambda v: v["aiGateway"]["bindings"][1].update(upstreamURL="https://provider.example.test/v1")),
            ("unsupported browser profile", lambda v: v["aiGateway"]["bindings"][0].update(profiles=["chatgpt-web-v1"])),
            ("unverified provider TLS", lambda v: v["aiGateway"]["bindings"][0].update(upstreamURL="http://provider.example.test/v1")),
            ("injected URL credentials", lambda v: v["aiGateway"]["bindings"][0].update(upstreamURL="https://key@provider.example.test/v1")),
            ("internal port collision", lambda v: v["aiGateway"].update(apiPort=443)),
            ("advertised API without HTTPS route", lambda v: v.update(externalGateway={"enabled": False})),
            ("retry override", lambda v: v["aiGateway"]["bindings"][0].update(retries=3)),
            ("credential-free is not automatically local", lambda v: v["aiGateway"]["bindings"][3].update(local=False)),
            ("invalid upstream port", lambda v: v["aiGateway"]["bindings"][3].update(upstreamURL="http://local.example.test:99999/v1")),
            ("ambiguous alias", lambda v: v["aiGateway"]["bindings"][0]["models"].append({"name": "provider-chat", "upstreamModel": "another-model"})),
            ("unqualified Google API mount", lambda v: v["aiGateway"]["bindings"][2].update(api=True)),
            ("provider/profile mismatch", lambda v: v["aiGateway"]["bindings"][0].update(profiles=["anthropic-messages-v1"])),
            ("old per-source endpoint schema", lambda v: v["aiGateway"]["bindings"][0].update(credentialSource="company")),
            ("duplicate canonical model", lambda v: v["aiGateway"]["bindings"][1]["models"][0].update(name="example-chat")),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                values = binding_values()
                mutate(values)
                self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
