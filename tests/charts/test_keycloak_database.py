"""Rendered contracts for the Keycloak PostgreSQL connection."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts/keycloak/server"
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"


def render() -> str:
    """Render the Keycloak server chart with repository synthetic values."""
    result = subprocess.run(
        [
            "helm",
            "template",
            "auth-keycloak",
            str(CHART),
            "--namespace",
            "auth-keycloak",
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


def resource(manifest: str, kind: str, name: str) -> str:
    """Return a rendered resource by kind and metadata name."""
    for document in re.split(r"(?m)^---\s*$", manifest):
        if f"kind: {kind}\n" in document and re.search(
            rf"(?m)^  name: {re.escape(name)}$", document
        ):
            return document
    raise AssertionError(f"Missing {kind} {name}")


class KeycloakDatabaseTests(unittest.TestCase):
    """Keep Keycloak on the dedicated external PostgreSQL provider."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = render()
        cls.stateful_set = resource(
            cls.manifest, "StatefulSet", "auth-keycloak-keycloak-stateful-set"
        )

    def test_jdbc_uses_external_database_with_verified_tls(self) -> None:
        expected_url = (
            "jdbc:postgresql://postgres-auth.infra-postgres-auth.svc.cluster.local:5432/"
            "keycloak?sslmode=verify-full&sslrootcert=/opt/keycloak/conf/truststores/"
            "postgres/ca.crt"
        )
        self.assertIn(f'name: KC_DB_URL\n              value: "{expected_url}"', self.stateful_set)
        self.assertIn('name: KC_DB_USERNAME\n              value: "keycloak"', self.stateful_set)
        self.assertRegex(
            self.stateful_set,
            r"(?s)name: KC_DB_PASSWORD.*?name: auth-keycloak-secret.*?key: dbPassword",
        )
        self.assertNotIn("KC_DB_URL_HOST", self.stateful_set)
        self.assertNotIn("KC_DB_URL_DATABASE", self.stateful_set)

    def test_internal_ca_bundle_is_mounted_at_the_jdbc_path(self) -> None:
        for contract in (
            "name: postgres-ca",
            'mountPath: "/opt/keycloak/conf/truststores/postgres"',
            'name: "infra-openbao-ca-bundle"',
            '- key: "ca.crt"',
            "path: ca.crt",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, self.stateful_set)
        self.assertNotIn("subPath:", self.stateful_set)

    def test_chart_no_longer_renders_an_embedded_postgresql(self) -> None:
        self.assertEqual(self.manifest.count("kind: StatefulSet\n"), 1)
        self.assertNotIn("auth-keycloak-postgresql", self.manifest)
        self.assertNotIn("kind: PersistentVolumeClaim", self.manifest)

    def test_release_depends_on_postgres_auth(self) -> None:
        release = (ROOT / "releases/keycloak/server.yaml").read_text(encoding="utf-8")
        self.assertRegex(
            release,
            r"dependsOn:(?s:.*?)name: postgres-auth\n      namespace: infra-postgres-auth",
        )
        self.assertNotIn("name: rook-ceph", release)


if __name__ == "__main__":
    unittest.main()
