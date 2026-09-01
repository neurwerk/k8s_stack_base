"""Rendered contracts for Keycloak Microsoft Active Directory federation."""

from __future__ import annotations

import copy
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"
AD_CHART = "charts/keycloak/realm-config/active-directory"
SERVER_CHART = "charts/keycloak/server"
VALID_AD: dict[str, Any] = {
    "enabled": True,
    "connectionUrl": "ldaps://ad.example.test:636",
    "usersDn": "OU=Users,DC=example,DC=test",
    "groupsDn": "OU=Groups,DC=example,DC=test",
    "usernameAttribute": "sAMAccountName",
    "groupNames": ["neurwerk-platform-admins", "neurwerk-studio.users"],
    "emailVerified": True,
    "caConfigMapName": "auth-keycloak-active-directory-ca",
    "caKey": "ca.crt",
    "egressCidrs": ["192.0.2.10/32"],
}


def helm(chart: str, active_directory: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
    """Render one Keycloak chart with optional AD values."""
    command = [
        "helm",
        "template",
        "active-directory-test",
        str(ROOT / chart),
        "--namespace",
        "auth-keycloak",
        "--values",
        str(LINT_VALUES),
    ]
    if active_directory is None:
        return subprocess.run(command, capture_output=True, text=True, check=False)

    with tempfile.TemporaryDirectory() as temporary_directory:
        values_path = Path(temporary_directory) / "active-directory.json"
        values_path.write_text(
            json.dumps({"authKeycloak": {"activeDirectory": active_directory}}),
            encoding="utf-8",
        )
        return subprocess.run(
            [*command, "--values", str(values_path)],
            capture_output=True,
            text=True,
            check=False,
        )


def render(chart: str, active_directory: dict[str, Any] | None = None) -> str:
    """Return a successful chart render."""
    result = helm(chart, active_directory)
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


class KeycloakActiveDirectoryTests(unittest.TestCase):
    """Keep disabled and enabled federation fail closed."""

    def test_disabled_chart_reconciles_without_ad_secret_or_ca_references(self) -> None:
        manifest = render(AD_CHART)
        job = resource(manifest, "Job", "auth-keycloak-active-directory-job")

        self.assertNotIn("kind: ExternalSecret", manifest)
        self.assertNotIn("auth-keycloak-active-directory-secret", manifest)
        self.assertNotIn("activeDirectoryBind", manifest)
        self.assertNotIn("KC_ACTIVE_DIRECTORY_BIND_", job)
        self.assertNotIn("active-directory-ca", manifest)
        self.assertIn('name: KC_ACTIVE_DIRECTORY_ENABLED\n              value: "false"', job)
        self.assertIn('command: ["upsert-active-directory"]', job)

    def test_enabled_chart_renders_exact_external_secret_and_job_contract(self) -> None:
        manifest = render(AD_CHART, VALID_AD)
        external_secret = resource(
            manifest, "ExternalSecret", "auth-keycloak-active-directory-secret"
        )
        job = resource(manifest, "Job", "auth-keycloak-active-directory-job")

        for contract in (
            "apiVersion: external-secrets.io/v1",
            "name: auth-keycloak-openbao-secret-store",
            "kind: SecretStore",
            "name: auth-keycloak-active-directory-secret",
            "creationPolicy: Owner",
            "deletionPolicy: Retain",
            "key: auth-keycloak/external",
            "property: activeDirectoryBindDn",
            "property: activeDirectoryBindCredential",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, external_secret)
        self.assertEqual(external_secret.count("key: auth-keycloak/external"), 2)
        self.assertEqual(external_secret.count("secretKey: activeDirectoryBind"), 2)

        expected_values = {
            "KC_HEALTH_PORT": "9000",
            "KC_ACTIVE_DIRECTORY_ENABLED": "true",
            "KC_ACTIVE_DIRECTORY_CONNECTION_URL": "ldaps://ad.example.test:636",
            "KC_ACTIVE_DIRECTORY_USERS_DN": "OU=Users,DC=example,DC=test",
            "KC_ACTIVE_DIRECTORY_GROUPS_DN": "OU=Groups,DC=example,DC=test",
            "KC_ACTIVE_DIRECTORY_USERNAME_ATTRIBUTE": "sAMAccountName",
            "KC_ACTIVE_DIRECTORY_EMAIL_VERIFIED": "true",
        }
        for name, value in expected_values.items():
            with self.subTest(environment=name):
                self.assertIn(f'name: {name}\n              value: "{value}"', job)
        self.assertIn(
            'name: KC_ACTIVE_DIRECTORY_GROUP_NAMES\n'
            '              value: "[\\"neurwerk-platform-admins\\",'
            '\\"neurwerk-studio.users\\"]"',
            job,
        )
        for name, key in (
            ("KC_ACTIVE_DIRECTORY_BIND_DN", "activeDirectoryBindDn"),
            ("KC_ACTIVE_DIRECTORY_BIND_CREDENTIAL", "activeDirectoryBindCredential"),
        ):
            self.assertRegex(
                job,
                rf"(?s)name: {name}.*?name: auth-keycloak-active-directory-secret.*?key: {key}",
            )

    def test_active_directory_job_has_finite_restricted_pod_security(self) -> None:
        job = resource(
            render(AD_CHART, VALID_AD), "Job", "auth-keycloak-active-directory-job"
        )
        for contract in (
            "activeDeadlineSeconds: 600",
            "automountServiceAccountToken: false",
            "runAsUser: 1000",
            "runAsGroup: 1000",
            "runAsNonRoot: true",
            "type: RuntimeDefault",
            "allowPrivilegeEscalation: false",
            "drop: [ALL]",
            "readOnlyRootFilesystem: true",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, job)

    def test_enabled_validation_rejects_every_incomplete_contract(self) -> None:
        invalid_cases: dict[str, tuple[str, Any]] = {
            "string enabled": ("enabled", "true"),
            "wrong scheme": ("connectionUrl", "ldap://ad.example.test:636"),
            "wrong port": ("connectionUrl", "ldaps://ad.example.test:389"),
            "url placeholder": ("connectionUrl", "ldaps://<host>:636"),
            "users placeholder": ("usersDn", "<users-dn>"),
            "empty groups dn": ("groupsDn", ""),
            "username attribute": ("usernameAttribute", "uid"),
            "empty groups": ("groupNames", []),
            "uppercase group": ("groupNames", ["neurwerk-Admins"]),
            "unprefixed group": ("groupNames", ["studio-users"]),
            "duplicate group": (
                "groupNames",
                ["neurwerk-studio-users", "neurwerk-studio-users"],
            ),
            "unverified email": ("emailVerified", False),
            "empty ca configmap": ("caConfigMapName", ""),
            "ca placeholder": ("caConfigMapName", "<ca-configmap>"),
            "uppercase ca configmap": ("caConfigMapName", "Auth-keycloak-ad-ca"),
            "underscore ca configmap": ("caConfigMapName", "auth_keycloak_ad_ca"),
            "overlong ca configmap label": ("caConfigMapName", f"{'a' * 64}.test"),
            "empty ca key": ("caKey", ""),
            "ca key slash": ("caKey", "certs/ca.crt"),
            "ca key space": ("caKey", "ca cert.crt"),
            "overlong ca key": ("caKey", "a" * 254),
            "empty cidrs": ("egressCidrs", []),
            "invalid cidr": ("egressCidrs", ["192.0.2.10"]),
            "ipv6 cidr": ("egressCidrs", ["2001:db8::/64"]),
            "malformed ipv6 cidr": ("egressCidrs", ["::::/64"]),
            "malformed ipv4 address": ("egressCidrs", ["192.0.2/32"]),
            "invalid ipv4 octet": ("egressCidrs", ["300.0.2.10/32"]),
            "leading-zero ipv4 octet": ("egressCidrs", ["192.000.2.10/32"]),
            "invalid ipv4 mask": ("egressCidrs", ["192.0.2.10/33"]),
        }
        for chart in (AD_CHART, SERVER_CHART):
            for name, (key, value) in invalid_cases.items():
                with self.subTest(chart=chart, case=name):
                    settings = copy.deepcopy(VALID_AD)
                    settings[key] = value
                    result = helm(chart, settings)
                    self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_enabled_validation_reports_ipv4_cidr_requirement(self) -> None:
        settings = copy.deepcopy(VALID_AD)
        settings["egressCidrs"] = ["::::/64"]
        for chart in (AD_CHART, SERVER_CHART):
            with self.subTest(chart=chart):
                result = helm(chart, settings)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("must be a valid IPv4 CIDR", result.stderr)

    def test_server_also_rejects_enabled_placeholder_values(self) -> None:
        settings = copy.deepcopy(VALID_AD)
        settings["groupsDn"] = "<groups-dn>"
        result = helm(SERVER_CHART, settings)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_server_trust_mount_is_conditional_and_public_ca_only(self) -> None:
        disabled = resource(
            render(SERVER_CHART), "StatefulSet", "auth-keycloak-keycloak-stateful-set"
        )
        enabled = resource(
            render(SERVER_CHART, VALID_AD),
            "StatefulSet",
            "auth-keycloak-keycloak-stateful-set",
        )

        for forbidden in (
            "KC_TRUSTSTORE_PATHS",
            "active-directory-ca",
            "configmap.reloader.stakater.com/reload",
        ):
            self.assertNotIn(forbidden, disabled)
        for contract in (
            "configmap.reloader.stakater.com/reload: \"auth-keycloak-active-directory-ca\"",
            "name: KC_TRUSTSTORE_PATHS",
            "value: /opt/keycloak/conf/truststores/active-directory",
            "mountPath: /opt/keycloak/conf/truststores/active-directory/ca.crt",
            "subPath: ca.crt",
            "readOnly: true",
            'name: "auth-keycloak-active-directory-ca"',
            '- key: "ca.crt"',
            "path: ca.crt",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, enabled)
        self.assertNotIn("optional: true", enabled)
        self.assertNotIn("pkcs12", enabled.lower())
        self.assertNotIn("truststore_password", enabled.lower())
        self.assertNotIn("tls.key", enabled)

    def test_release_identity_layers_and_dependency_are_exact(self) -> None:
        release = (ROOT / "releases/keycloak/active-directory.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("name: keycloak-active-directory", release)
        self.assertIn("releaseName: auth-keycloak-active-directory", release)
        self.assertIn("timeout: 15m", release)
        self.assertRegex(
            release,
            r"dependsOn:\n    - name: keycloak-realm-roles\n      namespace: auth-keycloak",
        )
        layers = (
            "base-shared-oidc-clients-config-map",
            "auth-keycloak-app-defaults",
            "auth-keycloak-secrets",
            "client-values",
            "keycloak-product-values",
        )
        positions = [release.index(f"name: {layer}") for layer in layers]
        self.assertEqual(positions, sorted(positions))
        kustomization = (ROOT / "releases/keycloak/kustomization.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("  - active-directory.yaml\n", kustomization)

    def test_agentgateway_group_grants_have_no_application_defaults(self) -> None:
        values = (
            ROOT / "charts/keycloak/realm-config/realm-roles/values.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("agentgatewayAccessGroups: {}", values)
        realm_roles = render("charts/keycloak/realm-config/realm-roles")
        self.assertNotIn('\\"clientRoles\\"', realm_roles)
        self.assertIn("/access/platform-admins", realm_roles)
        self.assertIn("librechat-user", realm_roles)

    def test_tooling_pin_and_chart_versions_are_coordinated(self) -> None:
        expected_versions = {
            "charts/keycloak/oidc/agentgateway": "0.6.6",
            "charts/keycloak/oidc/dify": "0.6.6",
            "charts/keycloak/oidc/dify-agentgateway": "0.6.7",
            "charts/keycloak/oidc/keycloak-api-key-bridge": "0.6.7",
            "charts/keycloak/oidc/librechat": "0.6.8",
            "charts/keycloak/oidc/studio": "0.6.7",
            "charts/keycloak/realm-config/active-directory": "0.1.1",
            "charts/keycloak/realm-config/initial-admin": "0.6.6",
            "charts/keycloak/realm-config/realm-roles": "0.6.6",
            "charts/keycloak/server": "0.6.12",
            "charts/opensearch": "0.6.11",
        }
        tooling_consumers = [
            path.parent
            for path in (ROOT / "charts").glob("**/values.yaml")
            if "k8s-stack-tooling" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(
            {path.relative_to(ROOT).as_posix() for path in tooling_consumers},
            set(expected_versions),
        )
        for path in tooling_consumers:
            with self.subTest(chart=path):
                values = (path / "values.yaml").read_text(encoding="utf-8")
                chart = (path / "Chart.yaml").read_text(encoding="utf-8")
                relative = path.relative_to(ROOT).as_posix()
                self.assertIn("ghcr.io/neurwerk/k8s-stack-tooling:0.1.0", values)
                self.assertRegex(
                    chart, rf"(?m)^version: {re.escape(expected_versions[relative])}$"
                )
                self.assertNotRegex(chart, r"(?m)^appVersion: [\"']?0\.1\.0")

    def test_keycloak_pin_includes_ldap_group_filter_fix(self) -> None:
        chart = (ROOT / "charts/keycloak/server/Chart.yaml").read_text(encoding="utf-8")
        values = (ROOT / "charts/keycloak/server/values.yaml").read_text(encoding="utf-8")

        self.assertRegex(chart, r'(?m)^appVersion: ["\']26\.7\.2["\']$')
        self.assertIn("keycloakImage: quay.io/keycloak/keycloak:26.7.2", values)
        self.assertNotIn("26.2.4", chart + values)


if __name__ == "__main__":
    unittest.main()
