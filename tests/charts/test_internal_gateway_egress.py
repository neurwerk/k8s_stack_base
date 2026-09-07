"""Rendered egress contracts for public names resolved to cluster Traefik."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / "tests/validation/helm-lint-values.yaml"


def render(chart: str, release: str, namespace: str, *extra_args: str) -> str:
    result = subprocess.run(
        [
            "helm",
            "template",
            release,
            str(ROOT / chart),
            "--namespace",
            namespace,
            "--values",
            str(VALUES),
            *extra_args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def resource(manifest: str, kind: str, name: str) -> str:
    matches = [
        document
        for document in re.split(r"(?m)^---\s*$", manifest)
        if re.search(rf"(?m)^kind:\s*{re.escape(kind)}\s*$", document)
        and re.search(rf"(?m)^  name:\s*{re.escape(name)}\s*$", document)
    ]
    if len(matches) != 1:
        raise AssertionError(f"Expected one {kind} {name}, found {len(matches)}")
    return matches[0]


TRAEFIK_HTTPS_EGRESS = """        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              app.kubernetes.io/name: traefik
      ports:
        - port: 443
          protocol: TCP"""


class InternalGatewayEgressTests(unittest.TestCase):
    def test_both_modes_retain_dns_and_private_dependencies(self) -> None:
        cases = (
            ("charts/dify/api", "frontend-dify-api", "frontend-dify",
             "frontend-dify-api-egress", (
                 ("postgres-operations", "infra-postgres-operations", 9712),
                 ("redis", None, 6379),
                 ("plugin-daemon", None, 5002),
                 ("sandbox", None, 8194),
                 ("infra-agentgateway-gateway", "infra-agentgateway", 80),
             )),
            ("charts/librechat/app", "frontend-librechat", "frontend-librechat",
             "frontend-librechat-network-policy", (
                 ("postgres-operations", "infra-postgres-operations", 10260),
                 ("frontend-librechat-meilisearch", None, 7700),
                 ("frontend-librechat-valkey", None, 6379),
                 ("infra-agentgateway-gateway", "infra-agentgateway", 80),
             )),
        )
        dns_rule = """        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP"""
        for chart, release, namespace, policy_name, dependencies in cases:
            for mode in ("internal-traefik", "public-dns"):
                with self.subTest(chart=chart, mode=mode):
                    policy = resource(render(
                        chart, release, namespace,
                        "--set", f"canonicalEndpointRouting.mode={mode}",
                    ), "NetworkPolicy", policy_name)
                    rules = policy.split("  egress:\n", 1)[1].split("    - to:\n")[1:]
                    with self.subTest(dependency="dns"):
                        self.assertEqual(rules[0].strip(), dns_rule.strip())
                    for name, target_namespace, port in dependencies:
                        with self.subTest(dependency=name):
                            matches = [rule for rule in rules if re.search(
                                rf"(?m)^              app.kubernetes.io/name: {re.escape(name)}$",
                                rule,
                            )]
                            self.assertEqual(len(matches), 1)
                            peer, ports = matches[0].split("      ports:\n", 1)
                            self.assertIn("podSelector:\n            matchLabels:", peer)
                            if target_namespace:
                                self.assertIn(
                                    "namespaceSelector:\n            matchLabels:\n"
                                    f"              kubernetes.io/metadata.name: {target_namespace}\n",
                                    peer,
                                )
                            else:
                                self.assertNotIn("namespaceSelector:", peer)
                            self.assertEqual(ports.strip(), f"- port: {port}\n          protocol: TCP")

    def test_optional_egress_flags_are_independent_in_both_modes(self) -> None:
        for mode in ("internal-traefik", "public-dns"):
            for rag in ("true", "false"):
                for interpreter in ("true", "false"):
                    with self.subTest(mode=mode, rag=rag, interpreter=interpreter):
                        policy = resource(render(
                            "charts/librechat/app", "frontend-librechat", "frontend-librechat",
                            "--set", f"canonicalEndpointRouting.mode={mode}",
                            "--set", f"frontendLibrechat.rag.enabled={rag}",
                            "--set", f"frontendLibrechat.codeInterpreter.enabled={interpreter}",
                        ), "NetworkPolicy", "frontend-librechat-network-policy")
                        self.assertEqual(TRAEFIK_HTTPS_EGRESS in policy, mode == "internal-traefik")
                        for name, port, enabled in (
                            ("frontend-librechat-rag-api", 8000, rag),
                            ("librechat-code-interpreter-api", 3112, interpreter),
                        ):
                            rule = (
                                f"              app.kubernetes.io/name: {name}\n"
                                f"      ports:\n        - port: {port}\n          protocol: TCP"
                            )
                            self.assertEqual(rule in policy, enabled == "true")

    def test_dify_api_defaults_to_traefik_and_retains_public_https(self) -> None:
        manifest = render("charts/dify/api", "frontend-dify-api", "frontend-dify")
        policy = resource(manifest, "NetworkPolicy", "frontend-dify-api-egress")

        self.assertIn(TRAEFIK_HTTPS_EGRESS, policy)
        self.assertIn("cidr: 0.0.0.0/0", policy)
        self.assertIn("- 10.0.0.0/8", policy)

    def test_librechat_defaults_to_traefik_and_retains_public_https(self) -> None:
        manifest = render(
            "charts/librechat/app", "frontend-librechat", "frontend-librechat"
        )
        policy = resource(
            manifest, "NetworkPolicy", "frontend-librechat-network-policy"
        )

        self.assertIn(TRAEFIK_HTTPS_EGRESS, policy)
        self.assertIn("cidr: 0.0.0.0/0", policy)
        self.assertIn("- 10.0.0.0/8", policy)

    def test_public_dns_mode_omits_traefik_and_retains_public_https(self) -> None:
        cases = (
            (
                "charts/dify/api",
                "frontend-dify-api",
                "frontend-dify",
                "frontend-dify-api-egress",
            ),
            (
                "charts/librechat/app",
                "frontend-librechat",
                "frontend-librechat",
                "frontend-librechat-network-policy",
            ),
        )
        for chart, release, namespace, policy_name in cases:
            with self.subTest(chart=chart):
                manifest = render(
                    chart,
                    release,
                    namespace,
                    "--set",
                    "canonicalEndpointRouting.mode=public-dns",
                )
                policy = resource(manifest, "NetworkPolicy", policy_name)

                self.assertNotIn(TRAEFIK_HTTPS_EGRESS, policy)
                self.assertIn("cidr: 0.0.0.0/0", policy)
                self.assertIn("- 10.0.0.0/8", policy)

    def test_unknown_routing_mode_fails_rendering(self) -> None:
        for chart, release, namespace in (
            ("charts/dify/api", "frontend-dify-api", "frontend-dify"),
            ("charts/librechat/app", "frontend-librechat", "frontend-librechat"),
        ):
            with self.subTest(chart=chart):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "canonicalEndpointRouting.mode must be internal-traefik or public-dns",
                ):
                    render(
                        chart,
                        release,
                        namespace,
                        "--set",
                        "canonicalEndpointRouting.mode=unknown",
                    )


if __name__ == "__main__":
    unittest.main()
