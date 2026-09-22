"""Rendered contract tests for initial Keycloak administrator onboarding."""

from __future__ import annotations

import unittest
from helm import render as helm_render


def render(*extra_args: str) -> str:
    return helm_render(
        "keycloak/realm-config/initial-admin",
        namespace="auth-keycloak",
        release="auth-keycloak-initial-admin",
        extra_args=extra_args,
    ).stdout


class KeycloakInitialAdminTests(unittest.TestCase):
    def test_email_hook_waits_for_public_issuer_after_user_creation(self) -> None:
        manifest = render()

        self.assertIn('"helm.sh/hook-weight": "-1"', manifest)
        self.assertIn("name: auth-keycloak-initial-admin-action-email-job", manifest)
        self.assertIn('"helm.sh/hook": post-install\n', manifest)
        self.assertIn('"helm.sh/hook-weight": "0"', manifest)
        self.assertIn("- send-user-actions-email", manifest)
        self.assertIn('value: "https://lint.example"', manifest)
        self.assertIn('name: KC_ACTION_EMAIL_LIFESPAN\n              value: "1800"', manifest)
        self.assertIn("name: auth-keycloak-initial-admin-action-email-egress", manifest)
        self.assertIn("app.kubernetes.io/component: configuration", manifest)
        self.assertIn("job: auth-keycloak-initial-admin-action-email-job", manifest)
        self.assertIn("- port: 443", manifest)
        self.assertIn(
            """        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              app.kubernetes.io/name: traefik""",
            manifest,
        )
        self.assertNotIn("ipBlock:", manifest)

    def test_email_hook_is_absent_without_smtp(self) -> None:
        manifest = render("--set", "authKeycloak.smtp.enabled=false")

        self.assertNotIn("auth-keycloak-initial-admin-action-email-job", manifest)
        self.assertNotIn("auth-keycloak-initial-admin-action-email-egress", manifest)

    def test_public_dns_mode_retains_public_https_egress(self) -> None:
        manifest = render("--set", "canonicalEndpointRouting.mode=public-dns")

        self.assertIn("name: auth-keycloak-initial-admin-action-email-egress", manifest)
        self.assertIn("ipBlock:", manifest)
        self.assertIn("cidr: 0.0.0.0/0", manifest)
        self.assertNotIn("app.kubernetes.io/name: traefik", manifest)

    def test_unknown_routing_mode_fails_rendering(self) -> None:
        with self.assertRaisesRegex(
            AssertionError,
            "canonicalEndpointRouting.mode must be internal-traefik or public-dns",
        ):
            render("--set", "canonicalEndpointRouting.mode=unknown")


if __name__ == "__main__":
    unittest.main()
