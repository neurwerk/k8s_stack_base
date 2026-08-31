"""Static checks for workload-level Reloader annotations."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"
CASES = (
    (
        "charts/pii-engine",
        "monitor-pii-engine",
        "monitor-pii-engine-deployment",
        "monitor-pii-engine-server-tls",
    ),
    (
        "charts/agentgateway-extproc",
        "monitor-agentgateway-extproc",
        "monitor-agentgateway-extproc-deployment",
        "monitor-agentgateway-extproc-engine-client-tls",
    ),
    (
        "charts/langfuse",
        "monitor-langfuse",
        "monitor-langfuse-otel-collector-deployment",
        "monitor-langfuse-secret",
    ),
)


def render(chart: str, namespace: str) -> str:
    """Render one chart with the repository's synthetic validation values."""
    result = subprocess.run(
        [
            "helm",
            "template",
            Path(chart).name,
            str(ROOT / chart),
            "--namespace",
            namespace,
            "--values",
            str(LINT_VALUES),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def deployment(manifest: str, name: str) -> str:
    """Return a rendered Deployment by metadata name."""
    for document in re.split(r"(?m)^---\s*$", manifest):
        if "kind: Deployment\n" in document and re.search(
            rf"(?m)^  name: {re.escape(name)}$", document
        ):
            return document
    raise AssertionError(f"Missing Deployment {name}")


class ReloaderAnnotationTests(unittest.TestCase):
    """Require named Secret reload instructions on workload metadata."""

    def test_secret_reload_annotations_are_on_deployment_metadata(self) -> None:
        for chart, namespace, deployment_name, secret_name in CASES:
            with self.subTest(chart=chart):
                resource = deployment(render(chart, namespace), deployment_name)
                metadata, separator, spec = resource.partition("\nspec:\n")

                self.assertTrue(separator, f"Missing spec in {deployment_name}")
                annotation = re.escape("secret.reloader.stakater.com/reload")
                self.assertRegex(
                    metadata,
                    rf'(?m)^    {annotation}: "?{re.escape(secret_name)}"?$',
                )
                self.assertNotIn("secret.reloader.stakater.com/reload", spec)


if __name__ == "__main__":
    unittest.main()
