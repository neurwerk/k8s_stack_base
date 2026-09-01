"""Rendered lifecycle contracts for Studio workloads."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"
RESOURCE_VALUES = ROOT / "releases/shared/resources.yaml"


class StudioLifecycleTests(unittest.TestCase):
    """Require health checks on the API that actually serves requests."""

    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(
            [
                "helm",
                "template",
                "studio-api",
                str(ROOT / "charts/studio/api"),
                "--namespace",
                "frontend-studio",
                "--values",
                str(LINT_VALUES),
                "--values",
                str(RESOURCE_VALUES),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        cls.manifest = result.stdout
        cls.deployment = next(
            document
            for document in re.split(r"(?m)^---\s*$", result.stdout)
            if "kind: Deployment\n" in document
            and "name: frontend-studio-api-deployment\n" in document
        )
        cls.network_policy = next(
            document
            for document in re.split(r"(?m)^---\s*$", result.stdout)
            if "kind: NetworkPolicy\n" in document
            and "name: frontend-studio-api-egress-network-policy\n" in document
        )

        web = subprocess.run(
            [
                "helm",
                "template",
                "studio",
                str(ROOT / "charts/studio/web"),
                "--namespace",
                "frontend-studio",
                "--values",
                str(LINT_VALUES),
                "--values",
                str(RESOURCE_VALUES),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if web.returncode != 0:
            raise RuntimeError(web.stderr)
        cls.web_manifest = web.stdout

    def test_main_api_has_local_lifecycle_probes(self) -> None:
        main_api = self.deployment.split("# --- Management server", maxsplit=1)[0]
        for probe, period, failures in (
            ("startupProbe", 5, 60),
            ("readinessProbe", 5, 3),
            ("livenessProbe", 10, 6),
        ):
            with self.subTest(probe=probe):
                self.assertRegex(
                    main_api,
                    rf"(?s){probe}:.*?path: /api/version.*?port: http"
                    rf".*?periodSeconds: {period}.*?failureThreshold: {failures}",
                )

    def test_release_images_are_pinned_to_0_1_0(self) -> None:
        self.assertIn("ghcr.io/neurwerk/k8s-stack-studio-api:0.1.0", self.deployment)
        self.assertIn("ghcr.io/neurwerk/k8s-stack-studio-web:0.1.0", self.web_manifest)

    def test_api_has_no_direct_agentgateway_configuration_or_egress(self) -> None:
        self.assertNotIn("K8S_STUDIO_AGENTGATEWAY_URL", self.deployment)
        self.assertNotIn("kubernetes.io/metadata.name: infra-agentgateway", self.network_policy)
        self.assertNotIn("app.kubernetes.io/name: infra-agentgateway-gateway", self.network_policy)

    def test_api_retains_pii_mtls_and_api_key_bridge_paths(self) -> None:
        for setting in (
            "K8S_STUDIO_PII_ENGINE_URL",
            "K8S_STUDIO_PII_ENGINE_CA_CERT",
            "K8S_STUDIO_PII_ENGINE_CLIENT_CERT",
            "K8S_STUDIO_PII_ENGINE_CLIENT_KEY",
            "K8S_STUDIO_KEYCLOAK_API_KEY_BRIDGE_URL",
        ):
            with self.subTest(setting=setting):
                self.assertIn(f"name: {setting}", self.deployment)
        self.assertIn("kubernetes.io/metadata.name: monitor-pii-engine", self.network_policy)
        self.assertIn("app.kubernetes.io/component: runtime", self.network_policy)
        self.assertIn("port: 8443", self.network_policy)
        self.assertIn("kubernetes.io/metadata.name: auth-keycloak-api-key-bridge", self.network_policy)
        self.assertIn("port: 8000", self.network_policy)


if __name__ == "__main__":
    unittest.main()
