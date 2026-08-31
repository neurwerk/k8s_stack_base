"""Static contracts for namespace-scoped OpenBao secret synchronization."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SYNC_ROOT = ROOT / "releases/openbao/secret-sync"
NAMESPACES = {
    "auth-keycloak",
    "auth-keycloak-api-key-bridge",
    "frontend-dify",
    "frontend-librechat",
    "frontend-studio",
    "infra-agentgateway",
    "infra-cert-manager",
    "infra-postgres-auth",
    "infra-postgres-operations",
    "monitor-fluent-bit",
    "monitor-kube-prometheus-stack",
    "monitor-langfuse",
    "monitor-opensearch",
    "monitor-pii-engine",
    "librechat-code-interpreter",
}
MAPPINGS = {
    "auth-keycloak-secrets": (
        "auth-keycloak/internal",
        {"adminPassword": "adminPassword", "dbPassword": "dbPassword"},
    ),
    "auth-keycloak-openbao-secret": (
        "auth-keycloak/internal",
        {"difyOidcClientSecret": "difyOidcClientSecret", "difyAgentgatewayClientSecret": "difyAgentgatewayClientSecret", "bridgeOidcClientSecret": "bridgeOidcClientSecret", "librechatOidcClientSecret": "librechatOidcClientSecret"},
    ),
    "auth-keycloak-smtp-secret": (
        "auth-keycloak/external",
        {"smtpUsername": "smtpUsername", "smtpPassword": "smtpPassword"},
    ),
    "auth-keycloak-api-key-bridge-openbao-secret": (
        "auth-keycloak-api-key-bridge/internal",
        {"keycloakClientSecret": "keycloakClientSecret", "difyAgentgatewayPrimaryVerifierSha256": "difyAgentgatewayPrimaryVerifierSha256", "difyAgentgatewaySecondaryVerifierSha256": "difyAgentgatewaySecondaryVerifierSha256"},
    ),
    "frontend-dify-runtime-secret": (
        "frontend-dify/internal",
        {name: name for name in ("secretKey", "postgresPassword", "redisPassword", "sandboxApiKey", "pluginDaemonKey")},
    ),
    "frontend-dify-openbao-secret": (
        "frontend-dify/internal",
        {"KEYCLOAK_OIDC_CLIENT_SECRET": "keycloakOidcClientSecret", "AUTO_SETUP_ADMIN_PASSWORD": "initPassword", "AGENTGATEWAY_API_KEY": "agentgatewayApiKey"},
    ),
    "frontend-librechat-runtime-secret": (
        "frontend-librechat/internal",
        {
            name: name
            for name in (
                "credsKey",
                "credsIv",
                "jwtSecret",
                "jwtRefreshSecret",
                "meiliMasterKey",
                "documentdbUser",
                "documentdbPassword",
                "openidSessionSecret",
                "openidClientSecret",
                "valkeyPassword",
                "ragPostgresqlUser",
                "ragPostgresqlPassword",
                "ragOpenaiApiKey",
                "adminPanelSessionSecret",
                "adminPanelMetricsSecret",
                "codeInterpreterJwtPrivateKey",
            )
        },
    ),
    "frontend-librechat-code-interpreter-runtime-secret": (
        "librechat-code-interpreter/internal",
        {
            name: name
            for name in (
                "internalServiceToken",
                "valkeyPassword",
                "egressGrantSecret",
                "executionManifestPrivateKey",
                "executionManifestPublicKey",
                "jwtPublicKey",
            )
        },
    ),
    "frontend-studio-openbao-secret": (
        "frontend-studio/internal",
        {"OPENSEARCH_PASSWORD": "opensearchPassword", "LANGFUSE_PUBLIC_KEY": "langfusePublicKey", "LANGFUSE_SECRET_KEY": "langfuseSecretKey"},
    ),
    "infra-agentgateway-secrets": (
        "infra-agentgateway/external",
        {name: name for name in ("openrouterApiKey", "deepseekApiKey", "braveApiKey")},
    ),
    "cert-manager-issuers-values": (
        "infra-cert-manager/external",
        {name: name for name in ("accessKeyId", "secretAccessKey")},
    ),
    "monitor-fluent-bit-shared-ingest-secret": (
        "monitor-fluent-bit/internal",
        {"ingestPassword": "ingestPassword"},
    ),
    "monitor-kube-prometheus-stack-secret": (
        "monitor-kube-prometheus-stack/internal",
        {"admin-user": "adminUser", "admin-password": "adminPassword"},
    ),
    "monitor-kube-prometheus-stack-smtp-secret": (
        "monitor-kube-prometheus-stack/external",
        {"smtpUsername": "smtpUsername", "smtpPassword": "smtpPassword"},
    ),
    "monitor-langfuse-secrets": (
        "monitor-langfuse/internal",
        {name: name for name in ("salt", "encryptionKey", "nextauthSecret", "postgresqlPassword", "clickhousePassword", "redisPassword", "initUserPassword", "initProjectPublicKey", "initProjectSecretKey")},
    ),
    "monitor-opensearch-secret": (
        "monitor-opensearch/internal",
        {name: name for name in ("adminPassword", "fluentBitPassword", "studioPassword")},
    ),
    "monitor-pii-engine-secrets": (
        "monitor-pii-engine/internal",
        {name: name for name in ("hashKey", "encryptionKey")},
    ),
    "postgres-auth-values": (
        "infra-postgres-auth/internal",
        {
            "adminPassword": "adminPassword",
            "keycloakPassword": "keycloakPassword",
        },
    ),
    "postgres-operations-values": (
        "infra-postgres-operations/internal",
        {
            name: name
            for name in (
                "adminPassword",
                "documentdbPassword",
                "difyPassword",
                "langfusePassword",
                "librechatRagPassword",
            )
        },
    ),
}
VALUE_TEMPLATES = {
    "auth-keycloak-secrets": (
        "authKeycloakSecrets",
        ("adminPassword", "dbPassword"),
    ),
    "frontend-dify-runtime-secret": (
        "difySecrets",
        ("SECRET_KEY", "POSTGRES_PASSWORD", "REDIS_PASSWORD", "SANDBOX_API_KEY", "PLUGIN_DAEMON_KEY"),
    ),
    "frontend-librechat-runtime-secret": (
        "frontendLibrechatSecrets",
        (
            "credsKey",
            "credsIv",
            "jwtSecret",
            "jwtRefreshSecret",
            "meiliMasterKey",
            "documentdbUser",
            "documentdbPassword",
            "openidSessionSecret",
            "openidClientSecret",
            "valkeyPassword",
            "ragPostgresqlUser",
            "ragPostgresqlPassword",
            "ragOpenaiApiKey",
            "adminPanelSessionSecret",
            "adminPanelMetricsSecret",
            "codeInterpreterJwtPrivateKey",
        ),
    ),
    "frontend-librechat-code-interpreter-runtime-secret": (
        "frontendLibrechatCodeInterpreterSecrets",
        (
            "internalServiceToken",
            "valkeyPassword",
            "egressGrantSecret",
            "executionManifestPrivateKey",
            "executionManifestPublicKey",
            "jwtPublicKey",
        ),
    ),
    "infra-agentgateway-secrets": (
        "infraAgentgatewayWrapperSecrets",
        ("openaiApiKey", "anthropicApiKey", "openrouterApiKey", "deepseekApiKey", "mcp", "brave", "apiKey"),
    ),
    "cert-manager-issuers-values": (
        "certManager",
        ("aws", "accessKeyId", "secretAccessKey"),
    ),
    "monitor-langfuse-secrets": (
        "monitorLangfuseWrapperSecrets",
        ("salt", "encryptionKey", "nextauthSecret", "postgresqlPassword", "clickhousePassword", "redisPassword", "initUserPassword", "initProjectPublicKey", "initProjectSecretKey"),
    ),
    "monitor-pii-engine-secrets": (
        "monitorPiiEngineSecrets",
        ("hashKey", "encryptionKey"),
    ),
    "postgres-auth-values": (
        "postgresAuthSecrets",
        ("adminPassword", "keycloakPassword"),
    ),
    "postgres-operations-values": (
        "postgresOperationsSecrets",
        (
            "adminPassword",
            "documentdbPassword",
            "difyPassword",
            "langfusePassword",
            "librechatRagPassword",
        ),
    ),
}


def build() -> str:
    """Build the standalone Kustomize root."""
    result = subprocess.run(
        ["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone", str(SYNC_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Secret sync build failed:\n{result.stderr}")
    return result.stdout


def resources(manifest: str) -> dict[tuple[str, str, str], str]:
    """Index rendered resources by kind, namespace, and name."""
    indexed = {}
    for document in re.split(r"(?m)^---\s*$", manifest):
        if not document.strip():
            continue
        metadata = re.search(r"(?ms)^metadata:\n(?P<body>.*?)(?=^\S|\Z)", document)
        kind = re.search(r"(?m)^kind: (\S+)$", document)
        if not metadata or not kind:
            raise AssertionError(f"Malformed rendered resource:\n{document[:300]}")
        name = re.search(r"(?m)^  name: (\S+)$", metadata["body"])
        namespace = re.search(r"(?m)^  namespace: (\S+)$", metadata["body"])
        if not name or not namespace:
            raise AssertionError(f"Resource lacks namespaced identity:\n{document[:300]}")
        indexed[(kind.group(1), namespace.group(1), name.group(1))] = document
    return indexed


class OpenBaoSecretSyncTests(unittest.TestCase):
    """Enforce the namespace-owned SecretStore and ExternalSecret contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = build()
        cls.resources = resources(cls.manifest)
        cls.external_secrets = {
            name: document
            for (kind, _namespace, name), document in cls.resources.items()
            if kind == "ExternalSecret"
        }

    def test_exact_service_accounts_and_secret_stores_are_rendered(self) -> None:
        service_accounts = {(namespace, name) for kind, namespace, name in self.resources if kind == "ServiceAccount"}
        stores = {(namespace, name) for kind, namespace, name in self.resources if kind == "SecretStore"}
        self.assertEqual(service_accounts, {(ns, f"{ns}-external-secrets") for ns in NAMESPACES})
        self.assertEqual(stores, {(ns, f"{ns}-openbao-secret-store") for ns in NAMESPACES})

    def test_every_store_has_exact_vault_tls_and_auth_contract(self) -> None:
        for namespace in NAMESPACES:
            document = self.resources[("SecretStore", namespace, f"{namespace}-openbao-secret-store")]
            expected_spec = f"""spec:
  provider:
    vault:
      auth:
        kubernetes:
          mountPath: kubernetes
          role: {namespace}
          serviceAccountRef:
            audiences:
            - openbao
            name: {namespace}-external-secrets
      caProvider:
        key: ca.crt
        name: infra-openbao-ca-bundle
        type: ConfigMap
      path: secret
      server: https://infra-openbao.infra-openbao.svc:8200
      version: v2"""
            with self.subTest(namespace=namespace):
                self.assertEqual(document[document.index("spec:"):].strip(), expected_spec)

    def test_exact_external_secret_targets_and_common_contract(self) -> None:
        self.assertEqual(set(self.external_secrets), set(MAPPINGS))
        for target, document in self.external_secrets.items():
            namespace = re.search(r"(?m)^  namespace: (\S+)$", document).group(1)
            with self.subTest(target=target):
                self.assertIn("refreshInterval: 1h", document)
                self.assertIn(f"name: {namespace}-openbao-secret-store", document)
                self.assertIn("kind: SecretStore", document)
                self.assertIn("creationPolicy: Owner", document)
                self.assertIn("deletionPolicy: Retain", document)
                self.assertIn(f"    name: {target}", document)
                self.assertIn("type: Opaque", document)

    def test_helm_values_targets_have_a_flux_watch_label(self) -> None:
        watched = {
            "auth-keycloak-secrets",
            "frontend-dify-runtime-secret",
            "frontend-dify-openbao-secret",
            "frontend-librechat-runtime-secret",
            "frontend-librechat-code-interpreter-runtime-secret",
            "infra-agentgateway-secrets",
            "cert-manager-issuers-values",
            "monitor-langfuse-secrets",
            "monitor-kube-prometheus-stack-smtp-secret",
            "monitor-pii-engine-secrets",
        }
        for target in watched:
            with self.subTest(target=target):
                self.assertIn("reconcile.fluxcd.io/watch: Enabled", self.external_secrets[target])

    def test_all_remote_properties_are_explicit_and_exact(self) -> None:
        pattern = re.compile(r"(?m)^  - remoteRef:\n      key: (\S+)\n      property: (\S+)\n    secretKey: (\S+)$")
        for target, (path, mappings) in MAPPINGS.items():
            actual = {(secret_key, remote_path, property_name) for remote_path, property_name, secret_key in pattern.findall(self.external_secrets[target])}
            expected = {(secret_key, path, property_name) for secret_key, property_name in mappings.items()}
            with self.subTest(target=target):
                self.assertEqual(actual, expected)

    def test_values_templates_use_v2_quoted_yaml_shapes(self) -> None:
        for target, (root_key, keys) in VALUE_TEMPLATES.items():
            document = self.external_secrets[target]
            template = document[document.index("        values.yaml: |"):]
            with self.subTest(target=target):
                self.assertIn("engineVersion: v2", document)
                self.assertEqual(
                    re.findall(r"(?m)^          ([A-Za-z][A-Za-z0-9]*):$", template),
                    [root_key],
                )
                for key in (root_key, *keys):
                    self.assertRegex(template, rf"(?m)^\s+{re.escape(key)}:")
                for line in template.splitlines():
                    if "{{" in line:
                        self.assertIn("| quote }}", line)
        langfuse = self.external_secrets["monitor-langfuse-secrets"]
        for forbidden in ("initOrg", "initProjectId", "initProjectName", "initUserEmail", "initUserName"):
            self.assertNotIn(forbidden, langfuse)

    def test_bridge_secret_contains_only_runtime_secret_and_verifiers(self) -> None:
        bridge = self.external_secrets["auth-keycloak-api-key-bridge-openbao-secret"]
        self.assertIn("engineVersion: v2", bridge)
        self.assertIn("KEYCLOAK_API_KEY_BRIDGE_KEYCLOAK_CLIENT_SECRET:", bridge)
        self.assertIn("difyAgentgatewayPrimaryVerifierSha256:", bridge)
        self.assertIn("difyAgentgatewaySecondaryVerifierSha256:", bridge)
        self.assertNotIn("primary.json:", bridge)
        self.assertNotIn("secondary.json:", bridge)
        self.assertNotIn('"permissions"', bridge)
        self.assertNotIn("DIFY_AGENTGATEWAY_API_KEY_SHA256", bridge)

    def test_monitoring_smtp_secret_exposes_only_helm_values(self) -> None:
        smtp = self.external_secrets["monitor-kube-prometheus-stack-smtp-secret"]

        self.assertIn("engineVersion: v2", smtp)
        self.assertIn("monitorKubePrometheusStack:", smtp)
        self.assertIn("authUsername: {{ .smtpUsername | quote }}", smtp)
        self.assertIn("authPassword: {{ .smtpPassword | quote }}", smtp)
        self.assertNotIn("smtp.tem.scaleway.com", smtp)

    def test_no_cluster_store_static_token_secret_or_studio_machine_key(self) -> None:
        kinds = {kind for kind, _namespace, _name in self.resources}
        self.assertNotIn("ClusterSecretStore", kinds)
        self.assertNotIn("Secret", kinds)
        self.assertNotIn("reloader.stakater.com", self.manifest)
        studio = self.external_secrets["frontend-studio-openbao-secret"]
        self.assertNotRegex(studio, r"(?i)agentgateway|machine")

    def test_secret_sync_is_in_the_infrastructure_stage(self) -> None:
        aggregate = (ROOT / "releases/infrastructure/kustomization.yaml").read_text(encoding="utf-8")
        self.assertRegex(aggregate, r"(?m)^\s*- ../openbao/secret-sync\s*$")


if __name__ == "__main__":
    unittest.main()
