"""Optional package boundaries and shared-service Forgejo contracts."""

import subprocess
import unittest

from test_openrouter_catalog import ROOT, env_value, render, resources


class ForgejoIntegrationTests(unittest.TestCase):
    def test_database_opt_in_and_retention(self):
        disabled = render("postgres/operations", {"forgejo": {"enabled": False}}).stdout
        self.assertNotIn("FORGEJO_PASSWORD", disabled)
        self.assertNotIn("forgejo-password:", disabled)
        missing = render("postgres/operations", {
            "forgejo": {"enabled": True},
            "postgresOperationsSecrets": {"forgejoPassword": ""},
        }, check=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("postgresOperationsSecrets.forgejoPassword is required", missing.stderr)
        enabled = render("postgres/operations", {
            "forgejo": {"enabled": True},
            "postgresOperationsSecrets": {"forgejoPassword": "fixture-only"},
        }).stdout
        for contract in (
            "CREATE DATABASE forgejo OWNER forgejo",
            "REVOKE ALL ON DATABASE forgejo FROM PUBLIC",
            'verify_isolation forgejo "$FORGEJO_PASSWORD" postgres template1',
            'verify_isolation librechat "$DOCUMENTDB_PASSWORD" forgejo',
            'verify_isolation dify "$DIFY_PASSWORD" forgejo',
            'verify_isolation agentgateway "$AGENTGATEWAY_PASSWORD" forgejo',
            'verify_isolation langfuse "$LANGFUSE_PASSWORD" forgejo',
            'verify_isolation librechat_rag "$LIBRECHAT_RAG_PASSWORD" forgejo',
            "has_database_privilege('operations_admin', 'forgejo', 'CONNECT')",
            "kubernetes.io/metadata.name: forgejo",
        ):
            self.assertIn(contract, enabled)
        self.assertNotIn("DROP DATABASE", enabled + disabled)
        self.assertNotIn("DROP ROLE", enabled + disabled)

    def test_private_certificate_profiles_and_oidc(self):
        values = {"forgejo": {"enabled": True, "hostname": "forgejo.example.com"},
                  "externalGateway": {"enabled": False}}
        policies = render("cert-manager/approval-policy", values).stdout
        for issuer in ("staging", "production"):
            self.assertIn(f"name: cert-manager-forgejo-{issuer}", policies)
            self.assertIn(f"- cert-manager-forgejo-{issuer}", policies)
        self.assertIn('"forgejo.example.com"', policies)
        disabled = render("cert-manager/approval-policy", {"forgejo": {"enabled": False}}).stdout
        self.assertNotIn("cert-manager-forgejo", disabled)
        oidc = render("keycloak/oidc/forgejo", values)
        self.assertEqual(env_value(oidc, "KC_REDIRECT_URI"),
                         "https://forgejo.example.com/user/oauth2/keycloak/callback")
        self.assertEqual(env_value(oidc, "KC_DIRECT_ACCESS_GRANTS_ENABLED"), "false")
        self.assertEqual(env_value(oidc, "KC_SERVICE_ACCOUNTS_ENABLED"), "false")
        self.assertIn("key: oidcClientSecret", oidc.stdout)
        self.assertIn('"claim.name": "forgejo_roles"', oidc.stdout)
        self.assertIn('"implicitFlowEnabled": False', oidc.stdout)
        self.assertFalse(resources(oidc, "Secret"))
        self.assertFalse(render("keycloak/oidc/forgejo", {"forgejo": {"enabled": False}}).stdout.strip())

    def test_optional_inventory_does_not_leak_into_default_stages(self):
        for stage in ("namespaces", "infrastructure", "applications"):
            result = subprocess.run([
                "kustomize", "build", "--load-restrictor", "LoadRestrictionsNone",
                str(ROOT / "releases" / stage),
            ], capture_output=True, text=True, check=True)
            self.assertNotIn("name: keycloak-forgejo-oidc", result.stdout)
            self.assertNotIn("name: forgejo-runtime", result.stdout)
            self.assertNotIn("name: forgejo\n", result.stdout)
        for stage in ("namespaces/forgejo", "forgejo/secret-sync", "forgejo/app"):
            subprocess.run([
                "kustomize", "build", "--load-restrictor", "LoadRestrictionsNone",
                str(ROOT / "releases" / stage),
            ], capture_output=True, text=True, check=True)
