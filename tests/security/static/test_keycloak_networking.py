"""Static and rendered contracts for auth-keycloak network isolation."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"
VALID_AD = {
    "enabled": True,
    "connectionUrl": "ldaps://ad.example.test:636",
    "usersDn": "OU=Users,DC=example,DC=test",
    "groupsDn": "OU=Groups,DC=example,DC=test",
    "usernameAttribute": "sAMAccountName",
    "groupNames": ["neurwerk-platform-admins"],
    "emailVerified": True,
    "caConfigMapName": "auth-keycloak-active-directory-ca",
    "caKey": "ca.crt",
    "egressCidrs": ["192.0.2.10/32"],
}
CONFIG_CHARTS = {
    "charts/keycloak/server": "auth-keycloak-init-egress",
    "charts/keycloak/oidc/agentgateway": "auth-keycloak-agentgateway-oidc-egress",
    "charts/keycloak/oidc/dify": "auth-keycloak-dify-oidc-egress",
    "charts/keycloak/oidc/dify-agentgateway": "auth-keycloak-dify-agentgateway-oidc-egress",
    "charts/keycloak/oidc/keycloak-api-key-bridge": "auth-keycloak-keycloak-api-key-bridge-oidc-network-policy",
    "charts/keycloak/oidc/librechat": "auth-keycloak-librechat-oidc-egress",
    "charts/keycloak/oidc/studio": "auth-keycloak-studio-oidc-egress",
    "charts/keycloak/realm-config/active-directory": "auth-keycloak-active-directory-egress",
    "charts/keycloak/realm-config/initial-admin": "auth-keycloak-initial-admin-egress",
    "charts/keycloak/realm-config/realm-roles": "auth-keycloak-realm-roles-egress",
}


def render(
    chart: str,
    active_directory: dict[str, object] | None = None,
    release_name: str | None = None,
) -> str:
    """Render a Keycloak chart with repository synthetic values."""
    command = [
        "helm",
        "template",
        release_name or Path(chart).name,
        str(ROOT / chart),
        "--namespace",
        "auth-keycloak",
        "--values",
        str(LINT_VALUES),
    ]
    if active_directory is None:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    else:
        with tempfile.TemporaryDirectory() as temporary_directory:
            values = Path(temporary_directory) / "active-directory.json"
            values.write_text(
                json.dumps({"authKeycloak": {"activeDirectory": active_directory}}),
                encoding="utf-8",
            )
            result = subprocess.run(
                [*command, "--values", str(values)],
                capture_output=True,
                text=True,
                check=False,
            )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def resource(manifest: str, kind: str, name: str) -> str:
    """Return a rendered resource by kind and metadata name."""
    for document in re.split(r"(?m)^---\s*$", manifest):
        if f"kind: {kind}\n" in document and re.search(
            rf"(?m)^  name: {re.escape(name)}$", document
        ):
            return document
    raise AssertionError(f"Missing {kind} {name}")


class KeycloakNetworkingTests(unittest.TestCase):
    """Require complete allowances under the namespace default deny."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = render("charts/keycloak/server")
        cls.server_with_ad = render("charts/keycloak/server", VALID_AD)

    def test_namespace_has_ingress_and_egress_default_deny(self) -> None:
        namespace = (ROOT / "releases/namespaces/keycloak.yaml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            namespace,
            r"(?s)kind: NetworkPolicy.*?name: default-deny.*?podSelector: \{\}.*?"
            r"policyTypes:\n    - Ingress\n    - Egress",
        )

    def test_every_keycloak_configuration_job_owns_dns_and_server_egress(self) -> None:
        for chart, policy_name in CONFIG_CHARTS.items():
            with self.subTest(chart=chart):
                manifest = render(chart)
                policy = resource(manifest, "NetworkPolicy", policy_name)
                self.assertIn("app.kubernetes.io/component: configuration", manifest)
                self.assertIn("kubernetes.io/metadata.name: kube-system", policy)
                self.assertIn("k8s-app: kube-dns", policy)
                self.assertIn("- port: 53", policy)
                self.assertIn("app: auth-keycloak-keycloak-app", policy)
                self.assertIn("- port: 8080", policy)
                self.assertIn("- port: 9000", policy)
                self.assertNotIn("- port: 80\n", policy)

    def test_keycloak_ingress_is_limited_to_declared_callers(self) -> None:
        policy = resource(
            self.server, "NetworkPolicy", "auth-keycloak-keycloak-ingress"
        )
        for contract in (
            "kubernetes.io/metadata.name: kube-system",
            "app.kubernetes.io/name: traefik",
            "app.kubernetes.io/component: configuration",
            "kubernetes.io/metadata.name: frontend-studio",
            "app.kubernetes.io/name: studio-api",
            "kubernetes.io/metadata.name: auth-keycloak-api-key-bridge",
            "app.kubernetes.io/name: auth-keycloak-api-key-bridge",
            "kubernetes.io/metadata.name: infra-agentgateway",
            "app.kubernetes.io/name: infra-agentgateway-gateway",
            "gateway.networking.k8s.io/gateway-name: infra-agentgateway-gateway",
            "- port: 8080",
            "- port: 9000",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, policy)
        self.assertNotIn("- port: 80\n", policy)

    def test_keycloak_ingress_allows_the_exact_agentgateway_jwks_callers(self) -> None:
        policy = resource(
            self.server, "NetworkPolicy", "auth-keycloak-keycloak-ingress"
        )
        gateway_peer = (
            "kubernetes.io/metadata.name: infra-agentgateway\n"
            "          podSelector:\n"
            "            matchLabels:\n"
            "              app.kubernetes.io/name: infra-agentgateway-gateway\n"
            "              gateway.networking.k8s.io/gateway-name: infra-agentgateway-gateway"
        )
        controller_peer = (
            "kubernetes.io/metadata.name: infra-agentgateway\n"
            "          podSelector:\n"
            "            matchLabels:\n"
            "              agentgateway: agentgateway\n"
            "              app.kubernetes.io/name: agentgateway\n"
            "              app.kubernetes.io/instance: infra-agentgateway"
        )
        self.assertIn(gateway_peer, policy)
        self.assertIn(controller_peer, policy)
        self.assertEqual(
            policy.count("kubernetes.io/metadata.name: infra-agentgateway"), 2
        )
        self.assertRegex(
            policy,
            r"(?s)" + re.escape(controller_peer) + r".*?ports:\n        - port: 8080",
        )

    def test_keycloak_egress_has_only_dns_postgresql_and_enabled_exceptions(self) -> None:
        disabled = resource(
            self.server, "NetworkPolicy", "auth-keycloak-keycloak-egress"
        )
        enabled = resource(
            self.server_with_ad, "NetworkPolicy", "auth-keycloak-keycloak-egress"
        )
        self.assertIn("k8s-app: kube-dns", disabled)
        self.assertIn("kubernetes.io/metadata.name: infra-postgres-auth", disabled)
        self.assertIn("app.kubernetes.io/name: postgres-auth", disabled)
        self.assertIn("app.kubernetes.io/instance: postgres-auth", disabled)
        self.assertIn("- port: 5432", disabled)
        self.assertNotIn("auth-keycloak-postgresql-app", disabled)
        self.assertNotIn("- port: 636", disabled)
        self.assertNotIn("192.0.2.10/32", disabled)
        self.assertIn('cidr: "192.0.2.10/32"', enabled)
        self.assertIn("- port: 636", enabled)

    def test_postgres_auth_ingress_accepts_only_the_keycloak_identity(self) -> None:
        policy = resource(
            render("charts/postgres/auth", release_name="postgres-auth"),
            "NetworkPolicy",
            "postgres-auth-ingress",
        )
        for contract in (
            "app.kubernetes.io/name: postgres-auth",
            "app.kubernetes.io/instance: postgres-auth",
            "kubernetes.io/metadata.name: auth-keycloak",
            "app: auth-keycloak-keycloak-app",
            "- port: 5432",
            "protocol: TCP",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, policy)
        self.assertNotIn("ipBlock:", policy)

    def test_smtp_egress_is_public_ipv4_only_on_the_configured_port(self) -> None:
        policy = resource(
            self.server, "NetworkPolicy", "auth-keycloak-keycloak-egress"
        )
        self.assertIn("cidr: 0.0.0.0/0", policy)
        for excluded in (
            "0.0.0.0/8",
            "10.0.0.0/8",
            "100.64.0.0/10",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "172.16.0.0/12",
            "192.0.0.0/24",
            "192.0.2.0/24",
            "192.168.0.0/16",
            "198.18.0.0/15",
            "198.51.100.0/24",
            "203.0.113.0/24",
            "224.0.0.0/4",
            "240.0.0.0/4",
        ):
            with self.subTest(cidr=excluded):
                self.assertIn(f"- {excluded}", policy)
        self.assertIn("- port: 465", policy)


if __name__ == "__main__":
    unittest.main()
