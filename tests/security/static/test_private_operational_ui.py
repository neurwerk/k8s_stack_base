"""Rendered contracts for private operational UIs."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CHARTS = {
    "grafana": ("charts/kube-prometheus-stack", "monitor-kube-prometheus-stack"),
    "langfuse": ("charts/langfuse", "monitor-langfuse"),
}


def render(chart: str, namespace: str) -> str:
    """Render a chart with its private defaults and vendored dependencies."""
    result = subprocess.run(
        [
            "helm",
            "template",
            namespace,
            str(ROOT / chart),
            "--namespace",
            namespace,
            "--skip-tests",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Helm render failed for {chart}:\n{result.stderr}")
    return result.stdout


def resource(manifest: str, kind: str, name: str) -> str:
    """Return a rendered resource by its Kubernetes identity."""
    for document in re.split(r"(?m)^---\s*$", manifest):
        if (
            f"kind: {kind}\n" in document
            and re.search(rf"(?m)^  name: {re.escape(name)}$", document)
        ):
            return document
    raise AssertionError(f"Missing {kind} {name}")


class PrivateOperationalUiTests(unittest.TestCase):
    """Keep direct Grafana and Langfuse access private until SSO exists."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.grafana = render(*CHARTS["grafana"])
        cls.langfuse = render(*CHARTS["langfuse"])

    def test_default_charts_render_no_public_route_resources(self) -> None:
        for application, manifest in {
            "Grafana": self.grafana,
            "Langfuse": self.langfuse,
        }.items():
            with self.subTest(application=application):
                self.assertNotRegex(manifest, r"(?m)^kind: (?:Ingress|Gateway|HTTPRoute)$")

    def test_grafana_is_only_reachable_through_its_clusterip_service(self) -> None:
        service = resource(
            self.grafana,
            "Service",
            "monitor-kube-prometheus-stack-grafana",
        )
        self.assertIn("  type: ClusterIP", service)

    def test_local_administrator_credentials_remain_secret_backed(self) -> None:
        grafana = resource(
            self.grafana,
            "Deployment",
            "monitor-kube-prometheus-stack-grafana",
        )
        self.assertIn("name: monitor-kube-prometheus-stack-secret", grafana)
        self.assertIn("key: admin-user", grafana)
        self.assertIn("key: admin-password", grafana)

        langfuse = resource(self.langfuse, "Deployment", "monitor-langfuse-web")
        self.assertIn("name: monitor-langfuse-secret", langfuse)
        self.assertIn("key: init-user-password", langfuse)


if __name__ == "__main__":
    unittest.main()
