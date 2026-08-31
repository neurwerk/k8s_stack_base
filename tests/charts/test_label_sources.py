"""Rendered label-source contracts for first-party chart resources."""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"
RESOURCE_VALUES = ROOT / "releases/shared/resources.yaml"


def render(
    chart_path: str,
    release_name: str,
    namespace: str,
    extra_values: Path | None = None,
) -> str:
    """Render one chart with an explicit Helm release identity."""
    command = [
        "helm",
        "template",
        release_name,
        str(ROOT / "charts" / chart_path),
        "--namespace",
        namespace,
        "--values",
        str(LINT_VALUES),
        "--values",
        str(RESOURCE_VALUES),
    ]
    if extra_values:
        command.extend(("--values", str(extra_values)))
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def resource(manifest: str, kind: str, name: str) -> str:
    """Return exactly one rendered resource by kind and metadata name."""
    matches = [
        document
        for document in re.split(r"(?m)^---\s*$", manifest)
        if f"kind: {kind}\n" in document
        and re.search(rf"(?m)^  name: {re.escape(name)}$", document)
    ]
    if len(matches) != 1:
        raise AssertionError(f"Expected one {kind} {name}, found {len(matches)}")
    return matches[0]


def mapping(document: str, key: str, indent: int) -> dict[str, str]:
    """Extract scalar values from one YAML mapping by indentation."""
    match = re.search(rf"(?m)^{' ' * indent}{re.escape(key)}:\s*$", document)
    if not match:
        raise AssertionError(f"Missing {key} mapping at indent {indent}")

    values: dict[str, str] = {}
    for line in document[match.end() :].splitlines():
        if not line.strip():
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= indent:
            break
        if line_indent == indent + 2:
            label, separator, value = line.strip().partition(":")
            if separator:
                values[label] = value.strip().strip('"')
    return values


def labels(document: str, indent: int) -> dict[str, str]:
    """Extract a labels mapping at the requested indentation."""
    return mapping(document, "labels", indent)


class LabelSourceTests(unittest.TestCase):
    """Keep chart query identities consistent across supported release names."""

    def test_extproc_service_monitor_uses_the_service_identity(self) -> None:
        expected = {
            "app.kubernetes.io/name": "monitor-agentgateway-extproc",
            "app.kubernetes.io/instance": "monitor-agentgateway-extproc",
        }
        for release_name in ("monitor-agentgateway-extproc", "synthetic-extproc"):
            with self.subTest(release=release_name):
                manifest = render(
                    "agentgateway-extproc",
                    release_name,
                    "monitor-agentgateway-extproc",
                )
                service = resource(
                    manifest, "Service", "monitor-agentgateway-extproc-service"
                )
                service_monitor = resource(
                    manifest,
                    "ServiceMonitor",
                    "monitor-agentgateway-extproc-service-monitor",
                )
                certificate = resource(
                    manifest,
                    "Certificate",
                    "monitor-agentgateway-extproc-engine-client-certificate",
                )

                self.assertEqual(expected, mapping(service_monitor, "matchLabels", 4))
                self.assertTrue(
                    mapping(service_monitor, "matchLabels", 4).items()
                    <= labels(service, 2).items()
                )
                self.assertEqual(
                    "monitor-agentgateway-extproc",
                    labels(service_monitor, 2)["app.kubernetes.io/instance"],
                )
                self.assertEqual(
                    "monitor-agentgateway-extproc",
                    labels(certificate, 2)["app.kubernetes.io/instance"],
                )

    def test_mcp_resources_share_the_workload_identity(self) -> None:
        values = """authKeycloak:
  agentgatewayClientRoles:
    - llm:invoke
    - model:remote/openrouter/deepseek-v4-flash:invoke
    - mcp:workload-tools:invoke
mcp:
  enabled: true
  approvedHosts: []
  servers:
    - name: workload-tools
      piiEnabled: true
      contentTracingEnabled: true
      port: 8080
      workload:
        image: registry.invalid/mcp-label-fixture:1.0.0
        resources: {}
infraAgentgatewayWrapperSecrets:
  mcp:
    workload-tools:
      apiKey: fixture
"""
        expected = {
            "app.kubernetes.io/name": "mcp-workload-tools",
            "app.kubernetes.io/instance": "infra-agentgateway",
            "app.kubernetes.io/part-of": "infra-agentgateway",
        }
        with tempfile.TemporaryDirectory() as directory:
            values_path = Path(directory) / "mcp-values.yaml"
            values_path.write_text(values, encoding="ascii")
            for release_name in ("infra-agentgateway", "synthetic-agentgateway"):
                with self.subTest(release=release_name):
                    manifest = render(
                        "agentgateway",
                        release_name,
                        "infra-agentgateway",
                        values_path,
                    )
                    for kind, name in (
                        (
                            "Deployment",
                            "mcp-workload-tools-deploy",
                        ),
                        ("Service", "mcp-workload-tools-svc"),
                        (
                            "NetworkPolicy",
                            "mcp-workload-tools-netpol",
                        ),
                        (
                            "AgentgatewayBackend",
                            "mcp-workload-tools-be",
                        ),
                        ("HTTPRoute", "mcp-workload-tools-route"),
                        (
                            "AgentgatewayPolicy",
                            "mcp-workload-tools-policy",
                        ),
                        ("Secret", "mcp-workload-tools-secret"),
                    ):
                        with self.subTest(kind=kind):
                            self.assertEqual(
                                expected, labels(resource(manifest, kind, name), 2)
                            )
                    expected_selector = {
                        "app.kubernetes.io/name": "mcp-workload-tools",
                        "app.kubernetes.io/instance": "infra-agentgateway",
                    }
                    deployment = resource(
                        manifest,
                        "Deployment",
                        "mcp-workload-tools-deploy",
                    )
                    service = resource(
                        manifest,
                        "Service",
                        "mcp-workload-tools-svc",
                    )
                    policy = resource(
                        manifest,
                        "NetworkPolicy",
                        "mcp-workload-tools-netpol",
                    )
                    self.assertEqual(expected_selector, mapping(deployment, "matchLabels", 4))
                    self.assertEqual(expected_selector, mapping(service, "selector", 2))
                    self.assertEqual(expected_selector, mapping(policy, "matchLabels", 4))

    def test_opensearch_network_policy_selects_upstream_pods(self) -> None:
        for release_name in ("monitor-opensearch", "synthetic-opensearch"):
            with self.subTest(release=release_name):
                manifest = render("opensearch", release_name, "monitor-opensearch")
                policy = resource(
                    manifest, "NetworkPolicy", "monitor-opensearch-network-policy"
                )
                stateful_set = resource(
                    manifest, "StatefulSet", "opensearch-cluster-master"
                )
                expected_selector = {
                    "app.kubernetes.io/name": "opensearch",
                    "app.kubernetes.io/instance": release_name,
                }

                self.assertEqual(expected_selector, mapping(policy, "matchLabels", 4))
                self.assertTrue(
                    mapping(policy, "matchLabels", 4).items()
                    <= labels(stateful_set, 6).items()
                )
                self.assertEqual(
                    release_name, labels(policy, 2)["app.kubernetes.io/instance"]
                )
                self.assertEqual(
                    "monitor-opensearch",
                    labels(stateful_set, 6)["app.kubernetes.io/part-of"],
                )
                for name in (
                    "monitor-opensearch-admin-certificate",
                    "monitor-opensearch-http-certificate",
                    "monitor-opensearch-transport-certificate",
                ):
                    self.assertEqual(
                        release_name,
                        labels(resource(manifest, "Certificate", name), 2)[
                            "app.kubernetes.io/instance"
                        ],
                    )

    def test_librechat_shared_configmap_uses_the_release_instance(self) -> None:
        manifest = render(
            "librechat/shared",
            "synthetic-librechat-shared",
            "frontend-librechat",
        )
        config_map = resource(manifest, "ConfigMap", "frontend-librechat-config-map")

        self.assertEqual(
            "synthetic-librechat-shared",
            labels(config_map, 2)["app.kubernetes.io/instance"],
        )

    def test_studio_api_uses_the_namespace_product_identity(self) -> None:
        manifest = render("studio/api", "synthetic-studio-api", "synthetic-studio")
        expected = {
            "app.kubernetes.io/name": "studio-api",
            "app.kubernetes.io/instance": "synthetic-studio-api",
            "app.kubernetes.io/part-of": "synthetic-studio",
        }
        for kind, name in (
            ("Deployment", "frontend-studio-api-deployment"),
            ("Service", "frontend-studio-api-service"),
            ("Certificate", "frontend-studio-pii-engine-client-certificate"),
            ("NetworkPolicy", "frontend-studio-api-egress-network-policy"),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(expected, labels(resource(manifest, kind, name), 2))
        expected_selector = {
            "app.kubernetes.io/name": "studio-api",
            "app.kubernetes.io/instance": "synthetic-studio-api",
        }
        deployment = resource(manifest, "Deployment", "frontend-studio-api-deployment")
        service = resource(manifest, "Service", "frontend-studio-api-service")
        policy = resource(
            manifest, "NetworkPolicy", "frontend-studio-api-egress-network-policy"
        )
        self.assertEqual(expected_selector, mapping(deployment, "matchLabels", 4))
        self.assertEqual(expected_selector, mapping(service, "selector", 2))
        self.assertEqual(expected_selector, mapping(policy, "matchLabels", 4))

    def test_langfuse_retention_resources_keep_the_product_identity(self) -> None:
        manifest = render("langfuse", "synthetic-langfuse", "synthetic-langfuse")
        expected = {
            "app.kubernetes.io/instance": "synthetic-langfuse",
            "app.kubernetes.io/part-of": "monitor-langfuse",
        }
        job = resource(manifest, "Job", "monitor-langfuse-init-retention-job")
        claim = resource(
            manifest,
            "ObjectBucketClaim",
            "monitor-langfuse-langfuse-object-bucket-claim",
        )

        self.assertTrue(expected.items() <= labels(job, 2).items())
        self.assertTrue(expected.items() <= labels(job, 6).items())
        self.assertTrue(expected.items() <= labels(claim, 2).items())


if __name__ == "__main__":
    unittest.main()
