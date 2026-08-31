"""Static contracts for declarative credential convergence and CA recovery."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"
PROVIDER_CHARTS = {
    "dify_oidc": "charts/keycloak/oidc/dify",
    "bridge_oidc": "charts/keycloak/oidc/keycloak-api-key-bridge",
    "librechat_oidc": "charts/keycloak/oidc/librechat",
    "opensearch": "charts/opensearch",
}
CONSUMER_CHARTS = {
    "bridge": "charts/keycloak-api-key-bridge",
    "dify": "charts/dify/api",
    "dify_shared": "charts/dify/shared",
    "librechat": "charts/librechat/app",
    "librechat_shared": "charts/librechat/shared",
    "studio": "charts/studio/api",
    "studio_shared": "charts/studio/shared",
    "fluent": "charts/fluent-bit",
    "fluent_shared": "charts/fluent-bit/shared",
    "langfuse": "charts/langfuse",
}


def render(path: str, namespace: str = "default") -> str:
    """Render one chart with deterministic synthetic values."""
    result = subprocess.run(
        [
            "helm",
            "template",
            Path(path).name,
            str(ROOT / path),
            "--namespace",
            namespace,
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


def documents(manifest: str) -> list[str]:
    """Split a rendered manifest into resources."""
    return [document for document in re.split(r"(?m)^---\s*$", manifest) if document.strip()]


def resource(manifest: str, kind: str, name: str) -> str:
    """Return a rendered resource by kind and metadata name."""
    for document in documents(manifest):
        if f"kind: {kind}\n" in document and re.search(
            rf"(?m)^  name: {re.escape(name)}$", document
        ):
            return document
    raise AssertionError(f"Missing {kind} {name}")


class DeclarativeCredentialTests(unittest.TestCase):
    """Require desired credentials to flow from ESO Secrets into providers and consumers."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.providers = {name: render(path) for name, path in PROVIDER_CHARTS.items()}
        cls.consumers = {name: render(path) for name, path in CONSUMER_CHARTS.items()}

    def test_credential_cronjobs_and_leases_are_absent(self) -> None:
        provider_resources = "\n".join(self.providers.values())
        cronjobs = [
            document
            for document in documents(provider_resources)
            if re.search(r"(?m)^kind: CronJob$", document)
        ]
        self.assertEqual(len(cronjobs), 1)
        self.assertIn("name: monitor-opensearch-ca-recovery-cron-job", cronjobs[0])
        self.assertNotRegex(provider_resources, r"(?m)^kind: Lease$")

        source = "\n".join(
            path.read_text(encoding="utf-8")
            for root in (ROOT / "charts", ROOT / "releases")
            for path in root.rglob("*.yaml")
        )
        for forbidden in (
            "credential-recovery",
            "oidc-recovery-cron-job",
            "user-recovery-cron-job",
            "studio-creds-recovery-cron-job",
            "dify-recovery-cron-job",
            "studio-recover-cron-job",
        ):
            self.assertNotIn(forbidden, source)

    def test_oidc_jobs_use_exact_desired_secret_inputs(self) -> None:
        expected = {
            "dify_oidc": "difyOidcClientSecret",
            "bridge_oidc": "bridgeOidcClientSecret",
            "librechat_oidc": "librechatOidcClientSecret",
        }
        for provider, key in expected.items():
            manifest = self.providers[provider]
            with self.subTest(provider=provider):
                self.assertRegex(
                    manifest,
                    rf"name: KC_CLIENT_SECRET\n\s+valueFrom:\n\s+secretKeyRef:\n\s+name: auth-keycloak-openbao-secret\n\s+key: {key}",
                )
                self.assertNotIn("KC_TARGET_", manifest)
                self.assertNotIn("RECONCILE_LOCK_", manifest)
                self.assertNotRegex(manifest, r"(?m)^kind: (?:Role|RoleBinding)$")

    def test_opensearch_jobs_use_exact_local_passwords(self) -> None:
        manifest = self.providers["opensearch"]
        expected = {
            "monitor-opensearch-init-opensearch-user-job": "fluentBitPassword",
            "monitor-opensearch-init-studio-logs-user-job": "studioPassword",
        }
        for name, key in expected.items():
            job = resource(manifest, "Job", name)
            with self.subTest(job=name):
                self.assertRegex(
                    job,
                    rf"name: INGEST_PASSWORD\n\s+valueFrom:\n\s+secretKeyRef:\n\s+name: monitor-opensearch-secret\n\s+key: {key}",
                )
                self.assertNotIn("TARGET_", job)
                self.assertNotIn("RECONCILE_LOCK_", job)

    def test_no_credential_secret_patch_rbac_remains(self) -> None:
        manifests = "\n".join((*self.providers.values(), *self.consumers.values()))
        for document in documents(manifests):
            if re.search(r"(?m)^kind: Role$", document) and 'resources: ["secrets"]' in document:
                self.assertNotRegex(document, r'verbs: \[[^\]]*"patch"')

    def test_consumers_require_the_expected_secret_keys(self) -> None:
        dify = resource(self.consumers["dify"], "Deployment", "frontend-dify-api-deployment")
        self.assertIn("name: frontend-dify-openbao-secret", dify)
        self.assertIn('name: ENFORCE_SINGLE_WORKSPACE\n              value: "true"', dify)
        self.assertIn('name: ALLOW_CREATE_WORKSPACE\n              value: "false"', dify)
        self.assertIn('name: ENABLE_EMAIL_PASSWORD_LOGIN\n              value: "false"', dify)
        self.assertIn('name: DEFAULT_WORKSPACE_NAME\n              value: "default"', dify)
        self.assertRegex(
            dify,
            r"name: LLM_PROXY_API_KEY\n\s+valueFrom:\n\s+secretKeyRef:\n\s+name: frontend-dify-openbao-secret\n\s+key: AGENTGATEWAY_API_KEY",
        )
        self.assertRegex(
            dify,
            r"name: AUTO_SETUP_ADMIN_PASSWORD\n\s+valueFrom:\n\s+secretKeyRef:\n\s+name: frontend-dify-openbao-secret\n\s+key: AUTO_SETUP_ADMIN_PASSWORD",
        )
        shared = resource(self.consumers["dify_shared"], "Secret", "frontend-dify-secret")
        self.assertNotIn("INIT_PASSWORD", shared)
        self.assertNotIn("optional: true", dify)

        librechat = resource(
            self.consumers["librechat_shared"],
            "Secret",
            "frontend-librechat-secret",
        )
        self.assertIn(
            'OPENID_CLIENT_SECRET: "lint-openid-client-secret"', librechat
        )
        librechat_deployment = resource(
            self.consumers["librechat"], "Deployment", "frontend-librechat"
        )
        self.assertIn(
            "secret.reloader.stakater.com/reload: frontend-librechat-secret",
            librechat_deployment,
        )
        librechat_config = resource(
            self.consumers["librechat_shared"],
            "ConfigMap",
            "frontend-librechat-config-map",
        )
        expected_env = {
            "ALLOW_REGISTRATION": "false",
            "ALLOW_EMAIL_LOGIN": "false",
            "ALLOW_SOCIAL_REGISTRATION": "false",
            "OPENID_REQUIRED_ROLE": "librechat-user",
            "OPENID_REQUIRED_ROLE_TOKEN_KIND": "access",
            "OPENID_REQUIRED_ROLE_PARAMETER_PATH": "realm_access.roles",
            "OPENID_ADMIN_ROLE": "librechat-admin",
            "OPENID_ADMIN_ROLE_TOKEN_KIND": "access",
            "OPENID_ADMIN_ROLE_PARAMETER_PATH": "realm_access.roles",
            "OPENID_REUSE_TOKENS": "true",
        }
        for name, value in expected_env.items():
            with self.subTest(name=name):
                self.assertRegex(
                    librechat_deployment,
                    rf"name: {name}\n\s+value: [\"']?{re.escape(value)}[\"']?",
                )
        self.assertIn(
            'Authorization: "Bearer {{LIBRECHAT_OPENID_ACCESS_TOKEN}}"',
            librechat_config,
        )

        studio = resource(
            self.consumers["studio"], "Deployment", "frontend-studio-api-deployment"
        )
        self.assertIn(
            'secret.reloader.stakater.com/reload: '
            '"frontend-studio-openbao-secret,frontend-studio-pii-engine-client-tls"',
            studio,
        )
        for key in ("OPENSEARCH_PASSWORD", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            self.assertRegex(
                studio,
                rf"name: K8S_STUDIO_{key}\n\s+valueFrom:\n\s+secretKeyRef:\n\s+name: frontend-studio-openbao-secret\n\s+key: {key}",
            )

        fluent = next(
            document
            for document in documents(self.consumers["fluent"])
            if re.search(r"(?m)^kind: DaemonSet$", document)
        )
        self.assertIn(
            "secret.reloader.stakater.com/reload: monitor-fluent-bit-shared-ingest-secret",
            fluent,
        )
        self.assertIn("name: monitor-fluent-bit-shared-ingest-secret", fluent)
        self.assertIn("key: ingestPassword", fluent)

    def test_managed_dify_key_is_file_backed_and_not_sqlite_bootstrapped(self) -> None:
        grants = resource(
            self.consumers["bridge"],
            "ConfigMap",
            "auth-keycloak-api-key-bridge-managed-key-grants",
        )
        bridge = resource(
            self.consumers["bridge"],
            "Deployment",
            "auth-keycloak-api-key-bridge-deployment",
        )
        self.assertIn("KEYCLOAK_API_KEY_BRIDGE_MANAGED_PRIMARY_GRANT_FILE", bridge)
        self.assertIn("KEYCLOAK_API_KEY_BRIDGE_MANAGED_PRIMARY_VERIFIER_FILE", bridge)
        self.assertIn("KEYCLOAK_API_KEY_BRIDGE_MANAGED_SECONDARY_GRANT_FILE", bridge)
        self.assertIn("KEYCLOAK_API_KEY_BRIDGE_MANAGED_SECONDARY_VERIFIER_FILE", bridge)
        self.assertIn("/var/run/managed-api-key-grants/primary.json", bridge)
        self.assertIn("/var/run/managed-api-key-grants/secondary.json", bridge)
        self.assertIn("/var/run/managed-api-key-verifiers/primary.sha256", bridge)
        self.assertIn("/var/run/managed-api-key-verifiers/secondary.sha256", bridge)
        self.assertIn("mountPath: /var/run/managed-api-key-grants", bridge)
        self.assertIn("mountPath: /var/run/managed-api-key-verifiers", bridge)
        self.assertNotIn("subPath:", bridge)
        self.assertIn("key: primary.json", bridge)
        self.assertIn("key: secondary.json", bridge)
        self.assertIn("key: difyAgentgatewayPrimaryVerifierSha256", bridge)
        self.assertIn("key: difyAgentgatewaySecondaryVerifierSha256", bridge)
        self.assertIn("name: auth-keycloak-api-key-bridge-managed-key-grants", bridge)
        self.assertIn("name: auth-keycloak-api-key-bridge-openbao-secret", bridge)
        self.assertIn('"version":2', grants)
        self.assertIn('"id":"dify-agentgateway-primary"', grants)
        self.assertIn('"client_id":"dify-agentgateway"', grants)
        self.assertIn(
            '"permissions":["llm:invoke","model:remote/openrouter/deepseek-v4-flash:invoke"]',
            grants,
        )
        self.assertNotIn("verifier", grants.lower())
        self.assertNotIn("kind: ExternalSecret", self.consumers["bridge"])
        self.assertRegex(bridge, r"(?s)livenessProbe:.*?path: /live")
        self.assertRegex(
            bridge,
            r"(?s)readinessProbe:.*?path: /health.*?periodSeconds: 10.*?timeoutSeconds: 2.*?failureThreshold: 1",
        )
        for setting in (
            "KEYCLOAK_API_KEY_BRIDGE_KEYCLOAK_URL",
            "KEYCLOAK_API_KEY_BRIDGE_KEYCLOAK_ISSUER",
            "KEYCLOAK_API_KEY_BRIDGE_KEYCLOAK_REALM",
            "KEYCLOAK_API_KEY_BRIDGE_KEYCLOAK_CLIENT_ID",
            "KEYCLOAK_API_KEY_BRIDGE_KEYCLOAK_CLIENT_SECRET",
            "KEYCLOAK_API_KEY_BRIDGE_DATABASE_URL",
            "KEYCLOAK_API_KEY_BRIDGE_MAX_KEYS_PER_USER",
            "KEYCLOAK_API_KEY_BRIDGE_LOG_LEVEL",
        ):
            self.assertIn(f"name: {setting}", bridge)
        self.assertNotIn("envFrom:", bridge)
        self.assertNotIn("ensure-api-key", "\n".join((*self.providers.values(), *self.consumers.values())))
        for path in (
            ROOT / "charts/keycloak-api-key-bridge/api-key",
        ):
            files = [item for item in path.rglob("*") if item.is_file()] if path.exists() else []
            self.assertEqual(files, [], path)

    def test_managed_dify_key_configuration_fails_closed(self) -> None:
        cases = (
            (
                "charts/dify/api",
                'authKeycloak.difyAgentgatewayClientRoles=["llm:invoke"]',
                'Dify model "remote/openrouter/deepseek-v4-flash" requires '
                '"model:remote/openrouter/deepseek-v4-flash:invoke"',
            ),
            (
                "charts/keycloak-api-key-bridge",
                'authKeycloak.difyAgentgatewayClientRoles=["llm:invoke","model:unlisted:invoke"]',
                'managed-key permission "model:unlisted:invoke" is absent from '
                "authKeycloak.agentgatewayClientRoles",
            ),
            (
                "charts/keycloak/oidc/dify-agentgateway",
                'authKeycloak.difyAgentgatewayClientRoles=["llm:invoke","llm:invoke"]',
                "authKeycloak.difyAgentgatewayClientRoles must not contain duplicates",
            ),
        )
        for chart, override, error in cases:
            result = subprocess.run(
                [
                    "helm",
                    "template",
                    "invalid-managed-key",
                    str(ROOT / chart),
                    "--values",
                    str(LINT_VALUES),
                    "--set-json",
                    override,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            with self.subTest(chart=chart):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)

    def test_bridge_metrics_are_scraped_by_prometheus(self) -> None:
        monitor = resource(
            self.consumers["bridge"],
            "ServiceMonitor",
            "auth-keycloak-api-key-bridge-service-monitor",
        )
        self.assertIn("app.kubernetes.io/name: auth-keycloak-api-key-bridge", monitor)
        self.assertIn("port: http", monitor)
        self.assertIn("path: /metrics", monitor)

    def test_studio_api_egress_selects_bridge_workload(self) -> None:
        """Keep the cross-namespace egress selector aligned with the bridge Pod identity."""
        studio_policy = resource(
            self.consumers["studio"],
            "NetworkPolicy",
            "frontend-studio-api-egress-network-policy",
        )
        bridge = resource(
            self.consumers["bridge"],
            "Deployment",
            "auth-keycloak-api-key-bridge-deployment",
        )
        bridge_name_label = "app.kubernetes.io/name: auth-keycloak-api-key-bridge"
        self.assertIn(bridge_name_label, studio_policy)
        self.assertIn(bridge_name_label, bridge)

    def test_bridge_runtime_has_no_kubernetes_token_or_privilege(self) -> None:
        """The bridge needs no Kubernetes API access and satisfies Restricted PSS."""
        manifest = self.consumers["bridge"]
        service_account = resource(
            manifest,
            "ServiceAccount",
            "auth-keycloak-api-key-bridge-service-account",
        )
        bridge = resource(
            manifest,
            "Deployment",
            "auth-keycloak-api-key-bridge-deployment",
        )
        self.assertIn("automountServiceAccountToken: false", service_account)
        self.assertIn("serviceAccountName: auth-keycloak-api-key-bridge-service-account", bridge)
        self.assertIn("automountServiceAccountToken: false", bridge)
        self.assertIn("runAsUser: 1000", bridge)
        self.assertIn("runAsGroup: 1000", bridge)
        self.assertIn("runAsNonRoot: true", bridge)
        self.assertIn("type: RuntimeDefault", bridge)
        self.assertIn("allowPrivilegeEscalation: false", bridge)
        self.assertIn('drop: ["ALL"]', bridge)
        self.assertIn("readOnlyRootFilesystem: true", bridge)
        self.assertNotRegex(manifest, r"(?m)^kind: (?:Role|RoleBinding)$")

    def test_studio_has_no_machine_key_or_runtime_secret_stub(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for root in (ROOT / "charts", ROOT / "releases")
            for path in root.rglob("*.yaml")
        )
        self.assertNotIn("K8S_STUDIO_AGENTGATEWAY_API_KEY", source)
        self.assertNotIn("AGENTGATEWAY_API_KEY_ID", source)
        self.assertNotIn("frontend-studio-shared-secret", source)
        self.assertNotRegex(source, r"(?m)^\s+(?:name|secretName): frontend-studio-secret$")
        self.assertNotIn("kind: Secret", self.consumers["studio_shared"])

    def test_langfuse_credentials_stay_in_secrets(self) -> None:
        manifest = self.consumers["langfuse"]
        init_config = resource(manifest, "ConfigMap", "monitor-langfuse-init-config-map")
        for key in ("org-id", "org-name", "project-id", "project-name", "user-email", "user-name"):
            self.assertRegex(init_config, rf"(?m)^  {key}: ")
        for forbidden in ("public-key", "secret-key", "password", "Basic JS"):
            self.assertNotIn(forbidden, init_config)
        otel_config = resource(
            manifest, "ConfigMap", "monitor-langfuse-otel-collector-config-map"
        )
        self.assertIn('Authorization: "Basic ${env:LANGFUSE_BASIC_AUTH}"', otel_config)
        self.assertNotIn("sync-langfuse-project-credentials", manifest)

        otel_deployment = resource(
            manifest, "Deployment", "monitor-langfuse-otel-collector-deployment"
        )
        self.assertIn(
            "secret.reloader.stakater.com/reload: monitor-langfuse-secret",
            otel_deployment,
        )


class OpenSearchCaRecoveryTests(unittest.TestCase):
    """Preserve the separate CA distribution and change-triggered rollouts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.opensearch = render("charts/opensearch", "monitor-opensearch")
        cls.studio = render("charts/studio/shared", "frontend-studio")
        cls.fluent = render("charts/fluent-bit/shared", "monitor-fluent-bit")

    def test_ca_recovery_cronjob_and_rollouts_are_preserved(self) -> None:
        cronjob = resource(
            self.opensearch, "CronJob", "monitor-opensearch-ca-recovery-cron-job"
        )
        self.assertIn('schedule: "*/15 * * * *"', cronjob)
        self.assertIn('sync_configmap("monitor-fluent-bit-shared-opensearch-ca"', cronjob)
        self.assertIn('restart("DaemonSet", "monitor-fluent-bit"', cronjob)
        self.assertIn('sync_configmap("frontend-studio-shared-opensearch-ca"', cronjob)
        self.assertIn('restart("Deployment", "frontend-studio-api-deployment"', cronjob)

    def test_ca_targets_and_only_ca_target_rbac_are_preserved(self) -> None:
        self.assertIn("name: frontend-studio-shared-opensearch-ca", self.studio)
        self.assertIn("name: monitor-fluent-bit-shared-opensearch-ca", self.fluent)
        for manifest in (self.studio, self.fluent):
            self.assertRegex(manifest, r"(?m)^kind: Role$")
            self.assertRegex(manifest, r"(?m)^kind: RoleBinding$")
            self.assertIn('resources: ["configmaps"]', manifest)
            self.assertNotIn('resources: ["secrets"]', manifest)
        self.assertIn("monitor-opensearch-ca-sync-service-account", self.studio)
        self.assertIn("monitor-opensearch-ca-recovery-service-account", self.studio)
        self.assertIn("monitor-opensearch-ca-sync-service-account", self.fluent)
        self.assertIn("monitor-opensearch-ca-recovery-service-account", self.fluent)

    def test_ca_sync_identities_cannot_read_the_private_ca_secret(self) -> None:
        self.assertNotIn('resources: ["secrets"]', self.opensearch)
        self.assertNotIn("read_namespaced_secret", self.opensearch)
        self.assertNotIn("infra-cert-manager-internal-ca-secret", self.opensearch)

        for role_name in (
            "infra-cert-manager-opensearch-ca-sync-role",
            "infra-cert-manager-opensearch-ca-recovery-source-role",
        ):
            role = resource(self.opensearch, "Role", role_name)
            self.assertIn('resources: ["configmaps"]', role)
            self.assertIn(
                'resourceNames: ["monitor-opensearch-ca-bundle"]',
                role,
            )
            self.assertIn('verbs: ["get"]', role)

        job = resource(self.opensearch, "Job", "monitor-opensearch-ca-sync-job")
        cronjob = resource(
            self.opensearch, "CronJob", "monitor-opensearch-ca-recovery-cron-job"
        )
        for workload in (job, cronjob):
            self.assertIn("read_namespaced_config_map", workload)
            self.assertIn("monitor-opensearch-ca-bundle", workload)
            self.assertIn("ca.crt", workload)


if __name__ == "__main__":
    unittest.main()
