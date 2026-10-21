"""Private analytics access for Studio and the gateway."""

import unittest

from helm import env_value, render, resource

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
    def test_logging_does_not_depend_on_guardrails_or_tracing(self):
        result = render("agentgateway", {
            "external-api-providers": [],
            "guardrails": {"llmPolicyEngine": {"enabled": False, "models": []}},
            "infraAgentgatewayWrapper": {"tracing": None},
        })
        raw = resource(result, "AgentgatewayParameters")["spec"]["rawConfig"]
        self.assertNotIn("http", raw["frontendPolicies"])
        self.assertNotIn("tracing", raw["config"])
        self.assertIn("database", raw["config"]["logging"])

    def test_database_and_studio_peers(self):
        postgres = render("postgres/operations", release="postgres-operations", namespace="infra-postgres-operations")
        rules = resource(postgres, "NetworkPolicy", "postgres-operations-ingress")["spec"]["ingress"]
        self.assertEqual([rule["ports"] for rule in rules if GATEWAY_PEER in rule["from"]],
                         [[{"port": 9712, "protocol": "TCP"}]])
        studio = render("studio/api", release="frontend-studio-api", namespace="frontend-studio")
        self.assertEqual(env_value(studio, "K8S_STUDIO_AGENTGATEWAY_ADMIN_URL"),
                         "http://infra-agentgateway-gateway.infra-agentgateway.svc.cluster.local:15000")
        self.assertEqual(env_value(studio, "K8S_STUDIO_USAGE_TIMEZONE"), "UTC")
        rules = resource(studio, "NetworkPolicy", "frontend-studio-api-egress-network-policy")["spec"]["egress"]
        self.assertIn({"to": [GATEWAY_PEER], "ports": [{"port": 15000, "protocol": "TCP"}]}, rules)
