"""Static checks for the declarative auth provider graph."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"


def render_chart(path: str) -> str:
    """Render a provider chart with shared synthetic values."""
    result = subprocess.run(
        [
            "helm",
            "template",
            "bootstrap-test",
            str(ROOT / path),
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


def dependencies(path: str) -> list[tuple[str, str]]:
    """Return HelmRelease dependencies in declaration order."""
    text = (ROOT / path).read_text(encoding="utf-8")
    if "  dependsOn:\n" not in text:
        return []
    block = text.split("  dependsOn:\n", maxsplit=1)[1].split("  chart:\n", maxsplit=1)[0]
    return re.findall(r"    - name: ([^\n]+)\n      namespace: ([^\n]+)", block)


class AuthBootstrapGraphTests(unittest.TestCase):
    """Keep provider convergence ordered without target-secret cycles."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.providers = {
            name: render_chart(path)
            for name, path in {
                "active_directory": "charts/keycloak/realm-config/active-directory",
                "agentgateway": "charts/keycloak/oidc/agentgateway",
                "dify": "charts/keycloak/oidc/dify",
                "dify_agentgateway": "charts/keycloak/oidc/dify-agentgateway",
                "bridge": "charts/keycloak/oidc/keycloak-api-key-bridge",
                "librechat": "charts/keycloak/oidc/librechat",
                "realm_roles": "charts/keycloak/realm-config/realm-roles",
                "initial_admin": "charts/keycloak/realm-config/initial-admin",
                "keycloak": "charts/keycloak/server",
                "studio": "charts/keycloak/oidc/studio",
            }.items()
        }

    def test_oidc_jobs_wait_only_for_local_keycloak_configuration(self) -> None:
        self.assertEqual(
            dependencies("releases/keycloak/oidc-dify.yaml"),
            [("keycloak", "auth-keycloak")],
        )
        self.assertEqual(
            dependencies("releases/keycloak/oidc-librechat.yaml"),
            [("keycloak", "auth-keycloak")],
        )
        self.assertEqual(
            dependencies("releases/keycloak/oidc-keycloak-api-key-bridge.yaml"),
            [
                ("keycloak", "auth-keycloak"),
                ("keycloak-realm-roles", "auth-keycloak"),
            ],
        )

    def test_keycloak_init_requires_a_realm(self) -> None:
        keycloak_init = ROOT / "charts/keycloak/server/templates/init/init-job.yaml"
        self.assertIn(
            'required "authKeycloak.realm is required"',
            keycloak_init.read_text(encoding="utf-8"),
        )

    def test_bridge_runtime_waits_for_its_oidc_provider(self) -> None:
        runtime = dependencies("releases/keycloak-api-key-bridge/app.yaml")
        self.assertIn(("keycloak-keycloak-api-key-bridge-oidc", "auth-keycloak"), runtime)
        self.assertIn(("keycloak-dify-agentgateway-oidc", "auth-keycloak"), runtime)
        self.assertNotIn(("keycloak-api-key-bridge-shared", "auth-keycloak-api-key-bridge"), runtime)
        self.assertEqual(len(runtime), len(set(runtime)))

    def test_bridge_uses_a_fresh_versioned_sqlite_claim(self) -> None:
        """A release must not attach the unversioned API-key database PVC."""
        pvc = (
            ROOT / "charts/keycloak-api-key-bridge/templates/pvc.yaml"
        ).read_text(encoding="utf-8")
        deployment = (
            ROOT / "charts/keycloak-api-key-bridge/templates/deployment.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("name: auth-keycloak-api-key-bridge-v2-pvc", pvc)
        self.assertIn('app.kubernetes.io/schema-version: "2"', pvc)
        self.assertNotIn("name: auth-keycloak-api-key-bridge-pvc", pvc)
        self.assertIn("claimName: auth-keycloak-api-key-bridge-v2-pvc", deployment)

    def test_librechat_waits_for_role_reconciliation(self) -> None:
        runtime = dependencies("releases/librechat/core/app.yaml")
        self.assertIn(("keycloak-librechat-oidc", "auth-keycloak"), runtime)
        self.assertIn(("keycloak-realm-roles", "auth-keycloak"), runtime)

    def test_keycloak_jobs_fit_inside_explicit_helm_timeouts(self) -> None:
        releases = (
            "active-directory.yaml",
            "server.yaml",
            "oidc-agentgateway.yaml",
            "oidc-dify.yaml",
            "oidc-dify-agentgateway.yaml",
            "oidc-keycloak-api-key-bridge.yaml",
            "oidc-librechat.yaml",
            "oidc-studio.yaml",
            "realm-initial-admin.yaml",
            "realm-roles.yaml",
        )
        for filename in releases:
            with self.subTest(filename=filename):
                release = (ROOT / "releases/keycloak" / filename).read_text(encoding="utf-8")
                self.assertIn("  timeout: 15m\n", release)

    def test_librechat_roles_are_issued_through_access_groups(self) -> None:
        manifest = self.providers["realm_roles"]
        for contract in (
            "librechat-user",
            "librechat-admin",
            "/access/librechat-users",
            "/access/librechat-admins",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, manifest)

    def test_agentgateway_oidc_does_not_wait_for_agentgateway_runtime(self) -> None:
        self.assertEqual(
            dependencies("releases/keycloak/oidc-agentgateway.yaml"),
            [("keycloak", "auth-keycloak")],
        )
        self.assertIn(
            ("keycloak-api-key-bridge", "auth-keycloak-api-key-bridge"),
            dependencies("releases/agentgateway/app.yaml"),
        )

    def test_retry_interval_is_nested_inside_flux_strategy(self) -> None:
        release = (
            ROOT / "releases/keycloak/oidc-dify-agentgateway.yaml"
        ).read_text(encoding="utf-8")
        self.assertEqual(release.count("      retryInterval: 1m"), 2)
        self.assertNotIn("\n    retryInterval: 1m", release)

    def test_agentgateway_runtimes_are_in_the_application_stage(self) -> None:
        infrastructure = (ROOT / "releases/infrastructure/kustomization.yaml").read_text(
            encoding="utf-8"
        )
        applications = (ROOT / "releases/applications/kustomization.yaml").read_text(
            encoding="utf-8"
        )
        for release in ("agentgateway", "agentgateway-extproc"):
            resource = f"  - ../{release}\n"
            self.assertNotIn(resource, infrastructure)
            self.assertIn(resource, applications)

    def test_gateway_roles_precede_client_owned_group_grants(self) -> None:
        self.assertIn("name: KC_CLIENT_ROLES", self.providers["agentgateway"])
        self.assertIn('\\"llm:invoke\\"', self.providers["agentgateway"])
        self.assertNotIn('\\"clientRoles\\"', self.providers["realm_roles"])
        self.assertEqual(
            dependencies("releases/keycloak/realm-roles.yaml"),
            [
                ("keycloak", "auth-keycloak"),
                ("keycloak-agentgateway-oidc", "auth-keycloak"),
            ],
        )

        self.assertEqual(
            dependencies("releases/keycloak/realm-initial-admin.yaml"),
            [
                ("keycloak", "auth-keycloak"),
                ("keycloak-realm-roles", "auth-keycloak"),
            ],
        )
        self.assertEqual(
            dependencies("releases/keycloak/active-directory.yaml"),
            [("keycloak-realm-roles", "auth-keycloak")],
        )

    def test_gateway_callers_declare_required_audiences(self) -> None:
        self.assertIn('value: \'["dify"]\'', self.providers["dify"])
        self.assertIn('value: \'["agentgateway"]\'', self.providers["librechat"])
        self.assertIn(
            'value: \'["realm-management", "keycloak-api-key-bridge"]\'',
            self.providers["studio"],
        )
        self.assertNotIn('"agentgateway"', self.providers["studio"])

    def test_dify_machine_identity_has_only_its_required_grant(self) -> None:
        machine = self.providers["dify_agentgateway"]
        self.assertIn('name: KC_STANDARD_FLOW_ENABLED\n              value: "false"', machine)
        self.assertIn('name: KC_DIRECT_ACCESS_GRANTS_ENABLED\n              value: "false"', machine)
        self.assertIn('name: KC_SERVICE_ACCOUNTS_ENABLED\n              value: "true"', machine)
        self.assertIn("name: KC_SERVICE_ACCOUNT_ROLES", machine)
        self.assertIn('roleName\\":\\"llm:invoke', machine)
        self.assertIn(
            'roleName\\":\\"model:remote/openrouter/deepseek-v4-flash:invoke',
            machine,
        )
        template = (
            ROOT / "charts/keycloak/oidc/dify-agentgateway/templates/job.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("authKeycloak.difyAgentgatewayClientRoles", template)
        self.assertIn("authKeycloak.agentgatewayClientRoles", template)
        self.assertNotIn("KC_SERVICE_ACCOUNT_REALM_ROLES", machine)

    def test_provider_jobs_have_no_kubernetes_api_permissions(self) -> None:
        for name, manifest in self.providers.items():
            with self.subTest(provider=name):
                self.assertNotRegex(manifest, r"(?m)^kind: (?:Role|RoleBinding)$")
                self.assertNotIn("KC_TARGET_", manifest)
                self.assertNotIn("RECONCILE_LOCK_", manifest)

    def test_human_onboarding_uses_email_actions_not_a_shared_password(self) -> None:
        initial_admin = self.providers["initial_admin"]
        keycloak = self.providers["keycloak"]
        self.assertNotIn("KC_INITIAL_USER_PASSWORD", initial_admin)
        self.assertNotIn("neurwerkAdminPassword", initial_admin)
        self.assertIn('value: "[\\\"VERIFY_EMAIL\\\", \\\"UPDATE_PASSWORD\\\", \\\"CONFIGURE_TOTP\\\"]"', initial_admin)
        self.assertIn('value: "true"', keycloak)
        self.assertIn("auth-keycloak-smtp-secret", keycloak)
        self.assertNotIn("smtpPassword:", keycloak)

    def test_keycloak_uses_one_fixed_public_issuer(self) -> None:
        """Internal callers must not change browser-link or token issuers."""
        keycloak = self.providers["keycloak"]
        for name, rendered_value in (
            ("KC_PROXY_HEADERS", "xforwarded"),
            ("KC_HOSTNAME", '"https://lint.example"'),
            ("KC_HOSTNAME_STRICT", '"true"'),
            ("KC_HOSTNAME_BACKCHANNEL_DYNAMIC", '"false"'),
        ):
            with self.subTest(name=name):
                self.assertIn(f"name: {name}\n              value: {rendered_value}", keycloak)

    def test_initial_admin_receives_the_complete_platform_role_contract(self) -> None:
        """Bootstrap the administrator through the all-platform access group."""
        self.assertIn(
            'name: KC_INITIAL_USER_GROUPS\n              value: "[\\"/access/platform-admins\\"]"',
            self.providers["initial_admin"],
        )

        values = (
            ROOT / "charts/keycloak/realm-config/realm-roles/values.yaml"
        ).read_text(encoding="utf-8")
        composite = values.split("    platform-admin:\n", maxsplit=1)[1].split(
            "\n\n", maxsplit=1
        )[0]
        self.assertEqual(
            set(re.findall(r"      - ([^\n]+)", composite)),
            {
                "keycloak-admin",
                "api-key-admin",
                "opensearch-admin",
                "langfuse-admin",
                "pii-admin",
                "studio-user",
                "librechat-admin",
                "dify-admin",
            },
        )

    def test_bridge_oidc_job_has_restricted_pod_security_and_egress(self) -> None:
        """The bridge client reconciler needs only DNS and Keycloak access."""
        manifest = self.providers["bridge"]
        self.assertIn("kind: ServiceAccount", manifest)
        self.assertIn("automountServiceAccountToken: false", manifest)
        self.assertIn("runAsUser: 1000", manifest)
        self.assertIn("runAsGroup: 1000", manifest)
        self.assertIn("runAsNonRoot: true", manifest)
        self.assertIn("type: RuntimeDefault", manifest)
        self.assertIn("allowPrivilegeEscalation: false", manifest)
        self.assertIn("drop: [ALL]", manifest)
        self.assertIn("readOnlyRootFilesystem: true", manifest)
        self.assertIn("kind: NetworkPolicy", manifest)
        self.assertIn("policyTypes: [Egress]", manifest)
        self.assertIn("kubernetes.io/metadata.name: kube-system", manifest)
        self.assertIn("kubernetes.io/metadata.name: auth-keycloak", manifest)
        self.assertIn("- port: 8080", manifest)
        self.assertIn("- port: 9000", manifest)
        self.assertNotIn("\n        - port: 80\n", manifest)

    def test_deleted_machine_key_releases_are_not_referenced(self) -> None:
        releases = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "releases").rglob("*.yaml")
        )
        self.assertNotIn("keycloak-dify-api-key", releases)
        self.assertNotIn("keycloak-studio-api-key", releases)
        self.assertNotIn("keycloak-api-key-bridge-shared", releases)


if __name__ == "__main__":
    unittest.main()
