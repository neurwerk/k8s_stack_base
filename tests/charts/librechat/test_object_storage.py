"""Rendered contracts for LibreChat shared configuration and applications."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from .helpers import (
    ROOT,
    non_secret_documents,
    render_chart,
    resource,
    resources_of_kind,
    secret_ref_names,
)


class LibreChatSharedAndAppTests(unittest.TestCase):
    """Keep shared data, app secrets, probes, and RGW behavior explicit."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_directory = tempfile.TemporaryDirectory(prefix="librechat-chart-")
        cls.temp_path = Path(cls.temp_directory.name)
        cls.secret_marker = "synthetic-librechat-secret-marker"
        cls.secret_fixture = cls.temp_path / "secret-values.yaml"
        cls.secret_fixture.write_text(
            f"""frontendLibrechatSecrets:
  credsKey: {cls.secret_marker}
""",
            encoding="ascii",
        )
        cls.disabled_storage_fixture = cls.temp_path / "storage-disabled.yaml"
        cls.disabled_storage_fixture.write_text(
            """frontendLibrechat:
  objectStorage:
    enabled: false
""",
            encoding="ascii",
        )
        cls.enabled_optional_features_fixture = cls.temp_path / "optional-features-enabled.yaml"
        cls.enabled_optional_features_fixture.write_text(
            """frontendLibrechat:
  rag:
    enabled: true
""",
            encoding="ascii",
        )
        cls.disabled_optional_features_fixture = cls.temp_path / "optional-features-disabled.yaml"
        cls.disabled_optional_features_fixture.write_text(
            """frontendLibrechat:
  codeInterpreter:
    enabled: false
  rag:
    enabled: false
""",
            encoding="ascii",
        )
        cls.coalesced_stream_fixture = cls.temp_path / "coalesced-stream.yaml"
        cls.coalesced_stream_fixture.write_text(
            """frontendLibrechat:
  app:
    streamDeltaCoalesceMs: 25
""",
            encoding="ascii",
        )

        cls.shared = render_chart(
            "shared",
            release_name="frontend-librechat-shared",
            values=(cls.secret_fixture,),
        ).stdout
        cls.app = render_chart(
            "app",
            release_name="frontend-librechat",
            values=(cls.enabled_optional_features_fixture,),
        ).stdout
        cls.shared_without_storage = render_chart(
            "shared",
            release_name="frontend-librechat-shared-no-rgw",
            values=(cls.disabled_storage_fixture,),
        ).stdout
        cls.shared_without_optional_features = render_chart(
            "shared",
            release_name="frontend-librechat-shared-no-optional-features",
            values=(cls.disabled_optional_features_fixture,),
        ).stdout
        cls.app_without_storage = render_chart(
            "app",
            release_name="frontend-librechat-no-rgw",
            values=(cls.disabled_storage_fixture,),
        ).stdout
        cls.app_with_coalescing = render_chart(
            "app",
            release_name="frontend-librechat-coalesced",
            values=(cls.coalesced_stream_fixture,),
        ).stdout

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_directory.cleanup()

    def test_shared_secret_and_configmap_are_separate_without_secret_leakage(self) -> None:
        secret = resource(self.shared, "Secret", "frontend-librechat-secret")
        admin_secret = resource(
            self.shared, "Secret", "frontend-librechat-admin-panel-secret"
        )
        config = resource(self.shared, "ConfigMap", "frontend-librechat-config-map")
        self.assertIn(self.secret_marker, secret)
        self.assertNotIn(self.secret_marker, config)
        self.assertNotIn(self.secret_marker, non_secret_documents(self.shared))
        self.assertEqual(len(resources_of_kind(self.shared, "Secret")), 2)
        self.assertEqual(len(resources_of_kind(self.shared, "ConfigMap")), 1)

        actual_keys = set(re.findall(r"(?m)^  ([A-Z][A-Z0-9_]+):", secret))
        self.assertEqual(
            actual_keys,
            {
                "CODEAPI_JWT_PRIVATE_KEY",
                "CREDS_IV",
                "CREDS_KEY",
                "DOMAIN_CLIENT",
                "DOMAIN_SERVER",
                "JWT_REFRESH_SECRET",
                "JWT_SECRET",
                "MEILI_MASTER_KEY",
                "MONGO_URI",
                "OPENID_CLIENT_ID",
                "OPENID_CLIENT_SECRET",
                "OPENID_ISSUER",
                "OPENID_SESSION_SECRET",
                "RAG_OPENAI_API_KEY",
                "RAG_POSTGRES_PASSWORD",
                "RAG_POSTGRES_USER",
                "REDIS_URI",
                "VALKEY_PASSWORD",
            },
        )
        self.assertEqual(
            set(re.findall(r"(?m)^  ([A-Z][A-Z0-9_]+):", admin_secret)),
            {"ADMIN_PANEL_METRICS_SECRET", "SESSION_SECRET"},
        )

    def test_disabled_agent_features_render_an_empty_capability_list(self) -> None:
        config = resource(
            self.shared_without_optional_features,
            "ConfigMap",
            "frontend-librechat-config-map",
        )
        self.assertIn("agents:\n        capabilities: []", config)

    def test_agentgateway_reasoning_and_stream_contract(self) -> None:
        config = resource(self.shared, "ConfigMap", "frontend-librechat-config-map")
        deployment = resource(self.app, "Deployment", "frontend-librechat")
        coalesced_deployment = resource(
            self.app_with_coalescing,
            "Deployment",
            "frontend-librechat",
        )

        self.assertIn("version: 1.3.14", config)
        self.assertIn("titleTiming: final", config)
        self.assertIn("reasoningKey: reasoning_content", config)
        self.assertIn("includeReasoningContent: true", config)
        self.assertIn("includeReasoningHistory: true", config)
        self.assertNotIn("useResponsesApi", config)
        self.assertRegex(
            deployment,
            r'name: STREAM_DELTA_COALESCE_MS\n\s+value: "0"',
        )
        self.assertRegex(
            coalesced_deployment,
            r'name: STREAM_DELTA_COALESCE_MS\n\s+value: "25"',
        )
        self.assertRegex(deployment, r'name: USE_REDIS_STREAMS\n\s+value: "true"')

    def test_app_has_distinct_startup_readiness_and_liveness_contracts(self) -> None:
        deployment = resource(self.app, "Deployment", "frontend-librechat")
        self.assertRegex(
            deployment,
            r"(?s)startupProbe:.*?path: /api/admin/oauth/openid/check.*?failureThreshold: 60",
        )
        self.assertRegex(
            deployment,
            r"(?s)readinessProbe:.*?path: /readyz.*?failureThreshold: 3",
        )
        self.assertRegex(
            deployment,
            r"(?s)livenessProbe:.*?path: /livez.*?failureThreshold: 6",
        )
        self.assertIn("automountServiceAccountToken: false", deployment)
        self.assertIn("readOnlyRootFilesystem: true", deployment)
        self.assertIn("drop:\n                - ALL", deployment)

    def test_app_waits_for_verified_openid_discovery(self) -> None:
        deployment = resource(self.app, "Deployment", "frontend-librechat")
        self.assertIn("name: wait-for-openid", deployment)
        self.assertIn("/.well-known/openid-configuration", deployment)
        self.assertIn("metadata.issuer !== issuer", deployment)
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            with self.subTest(field=field):
                self.assertIn(field, deployment)
        self.assertIn("AbortSignal.timeout(5000)", deployment)
        self.assertIn("attempt <= 30", deployment)
        self.assertNotIn("NODE_TLS_REJECT_UNAUTHORIZED", deployment)
        self.assertNotIn("rejectUnauthorized", deployment)
        self.assertRegex(
            deployment,
            r"(?s)name: wait-for-openid.*?allowPrivilegeEscalation: false"
            r".*?readOnlyRootFilesystem: true.*?resources:",
        )
        self.assertRegex(deployment, r"(?s)name: wait-for-openid.*?cpu: 10m")
        self.assertRegex(deployment, r"(?s)name: wait-for-openid.*?memory: 128Mi")
        self.assertRegex(
            deployment,
            r"(?s)name: OPENID_ISSUER.*?secretKeyRef:"
            r".*?name: frontend-librechat-secret.*?key: OPENID_ISSUER",
        )

    def test_app_uses_explicit_secret_refs_for_runtime_credentials(self) -> None:
        deployment = resource(self.app, "Deployment", "frontend-librechat")
        self.assertEqual(
            secret_ref_names(deployment),
            {
                "frontend-librechat-secret",
                "frontend-librechat-files-object-bucket-claim",
            },
        )
        for key in (
            "CREDS_KEY",
            "CREDS_IV",
            "JWT_SECRET",
            "JWT_REFRESH_SECRET",
            "MONGO_URI",
            "MEILI_MASTER_KEY",
            "REDIS_URI",
            "DOMAIN_CLIENT",
            "DOMAIN_SERVER",
            "OPENID_CLIENT_ID",
            "OPENID_CLIENT_SECRET",
            "OPENID_ISSUER",
            "OPENID_SESSION_SECRET",
            "CODEAPI_JWT_PRIVATE_KEY",
        ):
            with self.subTest(key=key):
                self.assertRegex(
                    deployment,
                    rf"name: {key}\n\s+valueFrom:\n\s+secretKeyRef:\n"
                    rf"\s+name: frontend-librechat-secret\n\s+key: {key}",
                )
        self.assertNotIn("envFrom:", deployment)

    def test_app_rag_admin_and_codeapi_environment_is_explicit(self) -> None:
        deployment = resource(self.app, "Deployment", "frontend-librechat")
        expected_values = {
            "RAG_API_URL": "http://frontend-librechat-rag-api-service:8000",
            "RAG_USE_FULL_CONTEXT": "false",
            "ADMIN_PANEL_URL": "https://librechat-admin.lint.example",
            "LIBRECHAT_CODE_BASEURL": (
                "http://librechat-code-interpreter-api."
                "librechat-code-interpreter.svc.cluster.local:3112/v1"
            ),
            "CODEAPI_AUTH_PROVIDER": "librechat-jwt",
            "CODEAPI_JWT_ALGORITHM": "EdDSA",
            "CODEAPI_JWT_AUDIENCE": "codeapi",
            "CODEAPI_JWT_ISSUER": "librechat",
            "CODEAPI_JWT_KID": "librechat-code-interpreter-v1",
            "CODEAPI_JWT_SINGLE_TENANT_ID": "librechat",
        }
        for name, value in expected_values.items():
            with self.subTest(name=name):
                self.assertRegex(
                    deployment,
                    rf"(?m)name: {name}\n\s+value: [\"']?"
                    rf"{re.escape(value)}[\"']?$",
                )

    def test_app_disables_rag_and_code_interpreter_by_default(self) -> None:
        deployment = resource(
            render_chart(
                "app",
                release_name="frontend-librechat",
                platform_values=False,
            ).stdout,
            "Deployment",
            "frontend-librechat",
        )

        self.assertNotIn("RAG_API_URL", deployment)
        self.assertNotIn("LIBRECHAT_CODE_BASEURL", deployment)

    def test_rgw_uses_all_file_strategies_and_retains_the_obc(self) -> None:
        config = resource(self.shared, "ConfigMap", "frontend-librechat-config-map")
        claim = resource(
            self.app,
            "ObjectBucketClaim",
            "frontend-librechat-files-object-bucket-claim",
        )
        deployment = resource(self.app, "Deployment", "frontend-librechat")
        policy = resource(
            self.app,
            "NetworkPolicy",
            "frontend-librechat-network-policy",
        )
        self.assertRegex(
            config,
            r"(?s)fileStrategy: s3\n\s+fileStrategies:\n\s+default: s3\n\s+avatar: s3\n"
            r"\s+image: s3\n\s+document: s3",
        )
        self.assertIn("helm.sh/resource-policy: keep", claim)
        self.assertIn("bucketName: librechat-files", claim)
        self.assertIn(
            "storageClassName: infra-rook-ceph-object-bucket",
            claim,
        )
        for name in (
            "AWS_ENDPOINT_URL",
            "AWS_REGION",
            "AWS_BUCKET_NAME",
            "AWS_FORCE_PATH_STYLE",
            "S3_URL_EXPIRY_SECONDS",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
        ):
            self.assertIn(f"name: {name}", deployment)
        self.assertIn(
            'name: AWS_ENDPOINT_URL\n              value: "https://objects.lint.example"',
            deployment,
        )
        self.assertIn(
            'name: S3_URL_EXPIRY_SECONDS\n              value: "3600"',
            deployment,
        )
        self.assertEqual(
            deployment.count(
                "name: frontend-librechat-files-object-bucket-claim"
            ),
            2,
        )
        self.assertNotIn("rook_object_store: infra-rook-ceph-object-store", policy)
        for cidr in (
            "10.0.0.0/8",
            "100.64.0.0/10",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "224.0.0.0/4",
        ):
            self.assertIn(f"- {cidr}", policy)

    def test_object_storage_rejects_a_non_https_browser_endpoint(self) -> None:
        result = render_chart(
            "app",
            release_name="frontend-librechat-insecure-object-endpoint",
            extra_args=(
                "--set-string",
                "frontendLibrechat.objectStorage.endpoint=http://rgw.example.test",
            ),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "LibreChat object storage endpoint must use browser-reachable HTTPS",
            result.stderr,
        )

    def test_local_storage_mode_removes_s3_config_credentials_and_egress(self) -> None:
        config = resource(
            self.shared_without_storage,
            "ConfigMap",
            "frontend-librechat-config-map",
        )
        deployment = resource(
            self.app_without_storage,
            "Deployment",
            "frontend-librechat",
        )
        policy = resource(
            self.app_without_storage,
            "NetworkPolicy",
            "frontend-librechat-network-policy",
        )
        self.assertNotIn("fileStrategies:", config)
        self.assertNotIn("kind: ObjectBucketClaim", self.app_without_storage)
        self.assertNotIn("AWS_ACCESS_KEY_ID", deployment)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", deployment)
        self.assertNotIn("rook_object_store:", policy)

    def test_local_image_claim_is_also_retained(self) -> None:
        claim = resource(
            self.app,
            "PersistentVolumeClaim",
            "frontend-librechat-images",
        )
        self.assertIn("helm.sh/resource-policy: keep", claim)
        self.assertIn("- ReadWriteOnce", claim)
        self.assertIn('storageClassName: "infra-rook-ceph-rbd"', claim)


class LibreChatAdminPanelTests(unittest.TestCase):
    """Keep the admin panel on a separate hostname and SSO-only boundary."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = render_chart(
            "admin-panel",
            release_name="frontend-librechat-admin-panel",
        ).stdout
        cls.app_manifest = render_chart(
            "app", release_name="frontend-librechat"
        ).stdout

    def test_admin_panel_has_its_own_gateway_hostname_and_route(self) -> None:
        gateway = resource(
            self.manifest,
            "Gateway",
            "frontend-librechat-admin-panel",
        )
        route = resource(
            self.manifest,
            "HTTPRoute",
            "frontend-librechat-admin-panel",
        )
        app_gateway = resource(
            self.app_manifest,
            "Gateway",
            "frontend-librechat-gateway",
        )
        self.assertIn('hostname: "librechat-admin.lint.example"', gateway)
        self.assertIn("name: frontend-librechat-admin-panel-tls", gateway)
        self.assertIn("name: frontend-librechat-admin-panel", route)
        self.assertIn('- "librechat-admin.lint.example"', route)
        self.assertIn("hostname: librechat.lint.example", app_gateway)
        self.assertNotIn("librechat-admin.lint.example", app_gateway)

    def test_admin_panel_is_sso_only_and_calls_the_internal_app(self) -> None:
        deployment = resource(
            self.manifest,
            "Deployment",
            "frontend-librechat-admin-panel",
        )
        self.assertIn(
            'name: API_SERVER_URL\n              value: "http://frontend-librechat:3080"',
            deployment,
        )
        self.assertIn(
            'name: VITE_API_BASE_URL\n              value: "https://librechat.lint.example"',
            deployment,
        )
        self.assertIn('name: ADMIN_SSO_ONLY\n              value: "true"', deployment)
        self.assertIn('name: ADMIN_SSO_ENABLED\n              value: "true"', deployment)
        self.assertNotRegex(deployment, r"(?i)name: .*password")
        self.assertNotRegex(deployment, r"(?i)name: .*username")
        self.assertIn("name: frontend-librechat-admin-panel-secret", deployment)
        self.assertNotIn("name: frontend-librechat-secret", deployment)

    def test_placeholder_admin_hostname_never_renders_a_public_route(self) -> None:
        manifest = render_chart(
            "admin-panel",
            release_name="frontend-librechat-admin-placeholder",
            platform_values=False,
            values=(ROOT / "releases/shared/hostnames.yaml",),
            extra_args=("--set", "externalGateway.enabled=true"),
        ).stdout
        self.assertEqual(resources_of_kind(manifest, "Gateway"), [])
        self.assertEqual(resources_of_kind(manifest, "HTTPRoute"), [])
        resource(
            manifest,
            "Deployment",
            "frontend-librechat-admin-placeholder-librechat-admin-panel",
        )

    def test_admin_metrics_use_a_bearer_secret_reference(self) -> None:
        manifest = render_chart(
            "admin-panel",
            release_name="frontend-librechat-admin-panel",
            extra_args=(
                "--set",
                "frontendLibrechat.adminPanel.serviceMonitor.enabled=true",
            ),
        ).stdout
        monitor = resource(
            manifest,
            "ServiceMonitor",
            "frontend-librechat-admin-panel",
        )
        self.assertIn("path: /metrics", monitor)
        self.assertIn("type: Bearer", monitor)
        self.assertIn("name: frontend-librechat-admin-panel-secret", monitor)
        self.assertIn("key: ADMIN_PANEL_METRICS_SECRET", monitor)


if __name__ == "__main__":
    unittest.main()
