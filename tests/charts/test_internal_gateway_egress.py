"""Keep private peers and their ports scoped in both endpoint routing modes."""

import unittest

from helm import render, resource


class InternalGatewayEgressTests(unittest.TestCase):
    def test_routing_modes_keep_dns_private_dependencies_and_public_https(self):
        for chart, policy_name, dependencies in (
            (
                "dify/api",
                "frontend-dify-api-egress",
                (
                    ("postgres-operations", "infra-postgres-operations", 9712),
                    ("redis", None, 6379),
                    ("plugin-daemon", None, 5002),
                    ("sandbox", None, 8194),
                    ("infra-agentgateway-gateway", "infra-agentgateway", 80),
                ),
            ),
            (
                "librechat/app",
                "frontend-librechat-network-policy",
                (
                    ("postgres-operations", "infra-postgres-operations", 10260),
                    ("frontend-librechat-meilisearch", None, 7700),
                    ("frontend-librechat-valkey", None, 6379),
                    ("infra-agentgateway-gateway", "infra-agentgateway", 80),
                ),
            ),
        ):
            for mode in (None, "public-dns"):
                with self.subTest(chart=chart, mode=mode or "default"):
                    values = {"canonicalEndpointRouting": {"mode": mode}} if mode else {}
                    rules = resource(render(chart, values), "NetworkPolicy", policy_name)["spec"][
                        "egress"
                    ]
                    dns = {
                        "namespaceSelector": {
                            "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                        },
                        "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                    }
                    self.assertIn(
                        {
                            "to": [dns],
                            "ports": [
                                {"port": 53, "protocol": "UDP"},
                                {"port": 53, "protocol": "TCP"},
                            ],
                        },
                        rules,
                    )
                    for name, namespace, port in (*dependencies, ("traefik", "kube-system", 443)):
                        matching = [
                            rule
                            for rule in rules
                            if any(
                                peer.get("podSelector", {})
                                .get("matchLabels", {})
                                .get("app.kubernetes.io/name")
                                == name
                                for peer in rule["to"]
                            )
                        ]
                        if name == "traefik" and mode == "public-dns":
                            self.assertEqual(matching, [])
                            continue
                        peer = {"podSelector": {"matchLabels": {"app.kubernetes.io/name": name}}}
                        if namespace:
                            peer["namespaceSelector"] = {
                                "matchLabels": {"kubernetes.io/metadata.name": namespace}
                            }
                        self.assertEqual(len(matching), 1)
                        self.assertEqual(matching[0]["ports"], [{"port": port, "protocol": "TCP"}])
                        self.assertEqual(len(matching[0]["to"]), 1)
                        self.assertEqual(
                            matching[0]["to"][0].get("namespaceSelector"),
                            peer.get("namespaceSelector"),
                        )
                    public = [
                        rule for rule in rules if any("ipBlock" in peer for peer in rule["to"])
                    ]
                    self.assertEqual(len(public), 1)
                    self.assertEqual(public[0]["ports"], [{"port": 443, "protocol": "TCP"}])
                    block = public[0]["to"][0]["ipBlock"]
                    self.assertEqual(block["cidr"], "0.0.0.0/0")
                    self.assertIn("10.0.0.0/8", block["except"])

    def test_optional_peers_are_independent(self):
        for mode in ("internal-traefik", "public-dns"):
            for rag, interpreter in ((True, False), (False, True)):
                with self.subTest(mode=mode, rag=rag, interpreter=interpreter):
                    result = render(
                        "librechat/app",
                        {
                            "canonicalEndpointRouting": {"mode": mode},
                            "frontendLibrechat": {
                                "rag": {"enabled": rag},
                                "codeInterpreter": {"enabled": interpreter},
                            },
                        },
                    )
                    rules = resource(result, "NetworkPolicy", "frontend-librechat-network-policy")[
                        "spec"
                    ]["egress"]
                    for name, port, enabled in (
                        ("frontend-librechat-rag-api", 8000, rag),
                        ("librechat-code-interpreter-api", 3112, interpreter),
                    ):
                        matching = [
                            rule
                            for rule in rules
                            if any(
                                peer.get("podSelector", {})
                                .get("matchLabels", {})
                                .get("app.kubernetes.io/name")
                                == name
                                for peer in rule["to"]
                            )
                        ]
                        peer = {"podSelector": {"matchLabels": {"app.kubernetes.io/name": name}}}
                        if port == 3112:
                            peer["namespaceSelector"] = {
                                "matchLabels": {
                                    "kubernetes.io/metadata.name": "librechat-code-interpreter"
                                }
                            }
                        self.assertEqual(
                            matching,
                            (
                                [{"to": [peer], "ports": [{"port": port, "protocol": "TCP"}]}]
                                if enabled
                                else []
                            ),
                        )

    def test_unknown_routing_mode_fails(self):
        for chart in ("dify/api", "librechat/app"):
            with (
                self.subTest(chart=chart),
                self.assertRaisesRegex(
                    AssertionError,
                    "canonicalEndpointRouting.mode must be internal-traefik or public-dns",
                ),
            ):
                render(chart, {"canonicalEndpointRouting": {"mode": "unknown"}})
