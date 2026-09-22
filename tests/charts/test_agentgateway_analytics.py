"""Metadata-only usage logging and the private Studio admin connection."""

import json
import unittest

import yaml

from helm import documents, env_value, render, resource

GATEWAY_PEER = {
    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "infra-agentgateway"}},
    "podSelector": {
        "matchLabels": {
            "app.kubernetes.io/name": "infra-agentgateway-gateway",
            "gateway.networking.k8s.io/gateway-name": "infra-agentgateway-gateway",
        }
    },
}


class AgentGatewayAnalyticsTests(unittest.TestCase):
    def test_metadata_logging_and_private_admin_listener(self):
        result = render("agentgateway")
        params = resource(result, "AgentgatewayParameters")["spec"]
        self.assertIn(
            {
                "name": "AGENTGATEWAY_DATABASE_PASSWORD",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": "infra-agentgateway-database-secret",
                        "key": "password",
                    },
                },
            },
            params["env"],
        )
        self.assertEqual(
            params["deployment"]["metadata"]["annotations"]["secret.reloader.stakater.com/reload"],
            "infra-agentgateway-database-secret",
        )
        self.assertEqual(params["service"]["spec"]["type"], "ClusterIP")
        self.assertIn(
            {"name": "admin", "port": 15000, "targetPort": 15000, "protocol": "TCP"},
            params["service"]["spec"]["ports"],
        )
        raw = params["rawConfig"]
        self.assertEqual(raw["frontendPolicies"]["accessLog"]["database"], {"llm": "metadata"})
        self.assertEqual(raw["config"]["adminAddr"], "0.0.0.0:15000")
        for source in ("has(jwt.sub)", "has(extauthz.principal_id)"):
            self.assertIn(source, raw["config"]["standardAttributes"]["user"])
        self.assertIn(
            "${AGENTGATEWAY_DATABASE_PASSWORD}", raw["config"]["logging"]["database"]["url"]
        )
        for doc in documents(result):
            if doc["kind"] != "Secret":
                self.assertNotIn("lint-agentgateway-database-password", json.dumps(doc))
            if doc["kind"] in ("Gateway", "HTTPRoute"):
                self.assertNotIn("15000", json.dumps(doc))
        policy = resource(result, "NetworkPolicy", "infra-agentgateway-data-plane-network-policy")[
            "spec"
        ]
        self.assertEqual(policy["policyTypes"], ["Ingress"])
        admin_rules = [
            rule
            for rule in policy["ingress"]
            if any(port["port"] == 15000 for port in rule.get("ports", []))
        ]
        self.assertEqual(
            admin_rules,
            [
                {
                    "from": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {"kubernetes.io/metadata.name": "frontend-studio"}
                            },
                            "podSelector": {
                                "matchLabels": {
                                    "app.kubernetes.io/name": "studio-api",
                                    "app.kubernetes.io/instance": "frontend-studio-api",
                                }
                            },
                        }
                    ],
                    "ports": [{"port": 15000, "protocol": "TCP"}],
                }
            ],
        )

    def test_verified_identity_stays_internal(self):
        result = render("agentgateway")
        auth = resource(result, "AgentgatewayPolicy", "infra-agentgateway-auth-ag-policy")["spec"][
            "traffic"
        ]
        http = auth["extAuth"]["conditional"][0]["policy"]["http"]
        for field in ("contract_version", "principal_id", "permissions"):
            self.assertEqual(
                http["responseMetadata"][field],
                f'json(response.headers["x-agentgateway-auth-context"]).{field}',
            )
        self.assertEqual(
            http["responseMetadata"]["groups"],
            '"groups" in json(response.headers["x-agentgateway-auth-context"]) ? json(response.headers["x-agentgateway-auth-context"]).groups : []',
        )
        self.assertNotIn("allowedResponseHeaders", http)
        self.assertNotIn("json(response.body)", json.dumps(http))
        expression = auth["authorization"]["policy"]["matchExpressions"][0]
        for check in (
            "type(extauthz.permissions) == list",
            "size(extauthz.principal_id) > 0",
            'has(extauthz.groups) && type(extauthz.groups) == list && extauthz.groups.all(g, type(g) == string && g.startsWith("/") && size(g) > 1)',
        ):
            self.assertIn(check, expression)
        stripping = resource(
            result, "AgentgatewayPolicy", "infra-agentgateway-remove-untrusted-identity-headers"
        )["spec"]["traffic"]["transformation"]
        for direction in ("request", "response"):
            self.assertIn("x-agentgateway-auth-context", stripping[direction]["remove"])

    def test_mcp_is_stateless_and_fails_closed_with_or_without_pii(self):
        for pii in (False, True):
            result = render(
                "agentgateway",
                {
                    "mcp": {
                        "enabled": True,
                        "approvedHosts": ["mcp.lint.example"],
                        "servers": [
                            {
                                "name": "example",
                                "host": "mcp.lint.example",
                                "port": 443,
                                "tls": True,
                                "piiEnabled": pii,
                                "contentTracingEnabled": False,
                            }
                        ],
                    },
                    "authKeycloak": {
                        "agentgatewayClientRoles": [
                            "llm:invoke",
                            "model:remote/example/model:invoke",
                            "mcp:example:invoke",
                        ]
                    },
                },
            )
            backend = yaml.safe_dump(resource(result, "AgentgatewayBackend", "mcp-example-be"))
            for field in (
                "sessionRouting: Stateless",
                "failureMode: FailClosed",
                "prefixMode: Always",
            ):
                self.assertIn(field, backend)
            policy = resource(result, "AgentgatewayPolicy", "mcp-example-policy")
            text = yaml.safe_dump(policy)
            for field in (
                "mcp:example:invoke",
                "failureMode: FailClosed",
                "responseBodyMode: FullDuplexStreamed",
            ):
                self.assertIn(field, text)
            self.assertIn(f"pii_enabled: '{str(pii).lower()}'", text)
            self.assertNotIn("mcp-session-id", text.lower())

    def test_logging_does_not_depend_on_guardrails_or_tracing(self):
        result = render(
            "agentgateway",
            {
                "guardrails": {"llmPolicyEngine": {"enabled": False, "models": []}},
                "infraAgentgatewayWrapper": {"tracing": None},
            },
        )
        raw = resource(result, "AgentgatewayParameters")["spec"]["rawConfig"]
        self.assertNotIn("http", raw["frontendPolicies"])
        self.assertNotIn("tracing", raw["config"])
        self.assertIn("database", raw["config"]["logging"])

    def test_database_and_studio_peers(self):
        postgres = render(
            "postgres/operations",
            release="postgres-operations",
            namespace="infra-postgres-operations",
        )
        rules = resource(postgres, "NetworkPolicy", "postgres-operations-ingress")["spec"][
            "ingress"
        ]
        self.assertEqual(
            [rule["ports"] for rule in rules if GATEWAY_PEER in rule["from"]],
            [[{"port": 9712, "protocol": "TCP"}]],
        )
        studio = render("studio/api", release="frontend-studio-api", namespace="frontend-studio")
        self.assertEqual(
            env_value(studio, "K8S_STUDIO_AGENTGATEWAY_ADMIN_URL"),
            "http://infra-agentgateway-gateway.infra-agentgateway.svc.cluster.local:15000",
        )
        self.assertEqual(env_value(studio, "K8S_STUDIO_USAGE_TIMEZONE"), "UTC")
        rules = resource(studio, "NetworkPolicy", "frontend-studio-api-egress-network-policy")[
            "spec"
        ]["egress"]
        self.assertIn({"to": [GATEWAY_PEER], "ports": [{"port": 15000, "protocol": "TCP"}]}, rules)
