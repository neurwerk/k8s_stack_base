"""Rendered egress contracts for public names resolved to cluster Traefik."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / "tests/validation/helm-lint-values.yaml"


def render(chart: str, release: str, namespace: str) -> str:
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
    def test_dify_api_allows_traefik_and_retains_public_https(self) -> None:
        manifest = render("charts/dify/api", "frontend-dify-api", "frontend-dify")
        policy = resource(manifest, "NetworkPolicy", "frontend-dify-api-egress")

        self.assertIn(TRAEFIK_HTTPS_EGRESS, policy)
        self.assertIn("cidr: 0.0.0.0/0", policy)
        self.assertIn("- 10.0.0.0/8", policy)

    def test_librechat_allows_traefik_and_retains_public_https(self) -> None:
        manifest = render(
            "charts/librechat/app", "frontend-librechat", "frontend-librechat"
        )
        policy = resource(
            manifest, "NetworkPolicy", "frontend-librechat-network-policy"
        )

        self.assertIn(TRAEFIK_HTTPS_EGRESS, policy)
        self.assertIn("cidr: 0.0.0.0/0", policy)
        self.assertIn("- 10.0.0.0/8", policy)


if __name__ == "__main__":
    unittest.main()
