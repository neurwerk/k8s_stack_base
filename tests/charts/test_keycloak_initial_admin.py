"""Rendered contract tests for initial Keycloak administrator onboarding."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts/keycloak/realm-config/initial-admin"
VALUES = ROOT / "tests/validation/helm-lint-values.yaml"


def render(*extra_args: str) -> str:
    result = subprocess.run(
        [
            "helm",
            "template",
            "auth-keycloak-initial-admin",
            str(CHART),
            "--namespace",
            "auth-keycloak",
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


class KeycloakInitialAdminTests(unittest.TestCase):
    def test_email_hook_waits_for_public_issuer_after_user_creation(self) -> None:
        manifest = render()
        release = (ROOT / "releases/keycloak/realm-initial-admin.yaml").read_text(
            encoding="ascii"
        )

        self.assertIn('"helm.sh/hook-weight": "-1"', manifest)
        self.assertIn("name: auth-keycloak-initial-admin-action-email-job", manifest)
        self.assertIn('"helm.sh/hook": post-install\n', manifest)
        self.assertIn('"helm.sh/hook-weight": "0"', manifest)
        self.assertIn("- send-user-actions-email", manifest)
        self.assertIn("image: \"ghcr.io/neurwerk/k8s-stack-tooling:0.1.1\"", manifest)
        self.assertIn("value: \"https://lint.example\"", manifest)
        self.assertIn("name: KC_ACTION_EMAIL_LIFESPAN\n              value: \"1800\"", manifest)
        self.assertIn("name: auth-keycloak-initial-admin-action-email-egress", manifest)
        self.assertIn("app.kubernetes.io/component: configuration", manifest)
        self.assertIn("job: auth-keycloak-initial-admin-action-email-job", manifest)
        self.assertIn("- port: 443", manifest)
        self.assertIn("name: RemediateOnFailure", release)
        self.assertIn("retries: -1", release)

    def test_email_hook_is_absent_without_smtp(self) -> None:
        manifest = render("--set", "authKeycloak.smtp.enabled=false")

        self.assertNotIn("auth-keycloak-initial-admin-action-email-job", manifest)
        self.assertNotIn("auth-keycloak-initial-admin-action-email-egress", manifest)


if __name__ == "__main__":
    unittest.main()
