"""Rendered contracts for LibreChat's shared database cutover."""

from __future__ import annotations

import re
import unittest

from .helpers import ROOT, render_chart, resource, resources_of_kind


WORKLOAD_COMPONENTS = {
    "valkey": (
        "frontend-librechat-valkey",
        "StatefulSet",
        "frontend-librechat-valkey-stateful-set",
        "frontend-librechat-valkey-service",
        "docker.io/valkey/valkey:9.1.1-alpine",
    ),
    "meilisearch": (
        "frontend-librechat-meilisearch",
        "StatefulSet",
        "frontend-librechat-meilisearch-stateful-set",
        "frontend-librechat-meilisearch-service",
        "docker.io/getmeili/meilisearch:v1.35.1",
    ),
    "rag-api": (
        "frontend-librechat-rag-api",
        "Deployment",
        "frontend-librechat-rag-api-deployment",
        "frontend-librechat-rag-api-service",
        "registry.librechat.ai/danny-avila/librechat-rag-api-dev-lite:v0.9.0",
    ),
    "app": (
        "frontend-librechat",
        "Deployment",
        "frontend-librechat",
        "frontend-librechat",
        "ghcr.io/danny-avila/librechat-dev@sha256:f309d33a0f0b22fe5d3a804c5d197f40d58e69f74d49b68f250cbc502da7e6b2",
    ),
    "admin-panel": (
        "frontend-librechat-admin-panel",
        "Deployment",
        "frontend-librechat-admin-panel",
        "frontend-librechat-admin-panel",
        "ghcr.io/clickhouse/librechat-admin-panel:1.0.0",
    ),
}


class LibreChatDatabaseCutoverTests(unittest.TestCase):
    """Keep LibreChat on the shared operations database boundaries."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifests = {
            chart: render_chart(chart, release_name=spec[0]).stdout
            for chart, spec in WORKLOAD_COMPONENTS.items()
        }
        cls.shared = render_chart(
            "shared", release_name="frontend-librechat-shared"
        ).stdout

    def test_surviving_workloads_keep_their_expected_services_and_images(self) -> None:
        for chart, (_release, kind, workload_name, service_name, image) in (
            WORKLOAD_COMPONENTS.items()
        ):
            manifest = self.manifests[chart]
            with self.subTest(chart=chart):
                workload = resource(manifest, kind, workload_name)
                service = resource(manifest, "Service", service_name)
                self.assertIn(f'image: "{image}"', workload)
                self.assertIn("type: ClusterIP", service)
                self.assertNotIn("type: LoadBalancer", service)
                self.assertNotIn("type: NodePort", service)

    def test_shared_secret_uses_verified_documentdb_gateway_uri(self) -> None:
        secret = resource(self.shared, "Secret", "frontend-librechat-secret")
        self.assertIn(
            "MONGO_URI: \"mongodb://lint:lint@postgres-operations."
            "infra-postgres-operations.svc.cluster.local:10260/LibreChat?"
            "authSource=admin&authMechanism=SCRAM-SHA-256&tls=true&"
            "tlsCAFile=%2Fetc%2Fdocumentdb%2Fca.crt&directConnection=true\"",
            secret,
        )
        self.assertNotIn("tlsAllowInvalid", secret)
        self.assertNotIn("FERRETDB_POSTGRESQL_URL", secret)
        self.assertNotIn("FERRETDB_POSTGRES_USER", secret)
        self.assertNotIn("FERRETDB_POSTGRES_PASSWORD", secret)

    def test_app_mounts_gateway_ca_and_allows_only_the_gateway_pod_port(self) -> None:
        deployment = resource(
            self.manifests["app"], "Deployment", "frontend-librechat"
        )
        policy = resource(
            self.manifests["app"],
            "NetworkPolicy",
            "frontend-librechat-network-policy",
        )

        self.assertRegex(
            deployment,
            r"(?s)- name: documentdb-ca\n"
            r"\s+mountPath: /etc/documentdb/ca.crt\n"
            r"\s+subPath: ca.crt\n"
            r"\s+readOnly: true",
        )
        self.assertRegex(
            deployment,
            r"(?s)- name: documentdb-ca\n"
            r"\s+configMap:\n"
            r"\s+name: infra-openbao-ca-bundle\n"
            r"\s+items:\n"
            r"\s+- key: ca.crt\n"
            r"\s+path: ca.crt",
        )
        self.assertIn(
            "kubernetes.io/metadata.name: infra-postgres-operations", policy
        )
        self.assertIn("app.kubernetes.io/name: postgres-operations", policy)
        self.assertIn("app.kubernetes.io/instance: postgres-operations", policy)
        self.assertRegex(policy, r"(?m)^\s+- port: 10260$")
        self.assertNotIn("frontend-librechat-ferretdb", policy)

    def test_rag_uses_plaintext_shared_postgresql_canary_boundary(self) -> None:
        deployment = resource(
            self.manifests["rag-api"],
            "Deployment",
            "frontend-librechat-rag-api-deployment",
        )
        policy = resource(
            self.manifests["rag-api"],
            "NetworkPolicy",
            "frontend-librechat-rag-api-network-policy",
        )

        self.assertIn(
            "name: DB_HOST\n"
            "              value: \"postgres-operations."
            "infra-postgres-operations.svc.cluster.local\"",
            deployment,
        )
        self.assertIn('name: DB_PORT\n              value: "5432"', deployment)
        self.assertIn('name: POSTGRES_DB\n              value: "librechat_rag"', deployment)
        self.assertIn('name: PGSSLMODE\n              value: disable', deployment)
        self.assertIn("key: RAG_POSTGRES_USER", deployment)
        self.assertIn("key: RAG_POSTGRES_PASSWORD", deployment)
        self.assertIn(
            "kubernetes.io/metadata.name: infra-postgres-operations", policy
        )
        self.assertIn("app.kubernetes.io/name: postgres-operations", policy)
        self.assertIn("app.kubernetes.io/instance: postgres-operations", policy)
        self.assertRegex(policy, r"(?m)^\s+- port: 9712$")
        self.assertNotRegex(policy, r"(?m)^\s+- port: 5432$")

    def test_rag_api_supports_both_pinned_image_variants(self) -> None:
        full_manifest = render_chart(
            "rag-api",
            release_name="frontend-librechat-rag-api-full",
            extra_args=(
                "--set-string",
                "frontendLibrechat.ragApi.image.variant=full",
            ),
        ).stdout
        full = resource(
            full_manifest,
            "Deployment",
            "frontend-librechat-rag-api-deployment",
        )
        self.assertIn(
            'image: "registry.librechat.ai/'
            'danny-avila/librechat-rag-api-dev:v0.9.0"',
            full,
        )

    def test_rag_image_variant_remains_closed(self) -> None:
        result = render_chart(
            "rag-api",
            release_name="frontend-librechat-rag-api-invalid",
            extra_args=(
                "--set-string",
                "frontendLibrechat.ragApi.image.variant=debug",
            ),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "frontendLibrechat.ragApi.image.variant",
            result.stderr.replace("/", "."),
        )

    def test_valkey_and_meilisearch_remain_private_and_persistent(self) -> None:
        valkey = resource(
            self.manifests["valkey"],
            "StatefulSet",
            "frontend-librechat-valkey-stateful-set",
        )
        meilisearch = resource(
            self.manifests["meilisearch"],
            "StatefulSet",
            "frontend-librechat-meilisearch-stateful-set",
        )
        meilisearch_policy = resource(
            self.manifests["meilisearch"],
            "NetworkPolicy",
            "frontend-librechat-meilisearch-network-policy",
        )

        self.assertIn("requirepass %s", valkey)
        self.assertIn("appendonly yes", valkey)
        self.assertIn("whenDeleted: Retain", valkey)
        self.assertIn("whenScaled: Retain", valkey)
        self.assertIn("name: MEILI_MASTER_KEY", meilisearch)
        self.assertIn("whenDeleted: Retain", meilisearch)
        self.assertIn("whenScaled: Retain", meilisearch)
        self.assertNotIn("ipBlock:", meilisearch_policy)

    def test_shared_chart_still_owns_only_config_and_scoped_secrets(self) -> None:
        self.assertEqual(len(resources_of_kind(self.shared, "Secret")), 2)
        self.assertEqual(len(resources_of_kind(self.shared, "ConfigMap")), 1)
        self.assertEqual(resources_of_kind(self.shared, "Deployment"), [])
        self.assertEqual(resources_of_kind(self.shared, "StatefulSet"), [])
        self.assertEqual(resources_of_kind(self.shared, "Service"), [])


class LibreChatDatabaseReleaseTests(unittest.TestCase):
    """Keep release ordering aligned with the shared database provider."""

    def test_release_kustomizations_contain_only_surviving_components(self) -> None:
        core = (ROOT / "releases/librechat/core/kustomization.yaml").read_text(
            encoding="utf-8"
        )
        rag = (ROOT / "releases/librechat/rag/kustomization.yaml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            re.findall(r"(?m)^  - ([a-z0-9-]+\.yaml)$", core),
            [
                "shared.yaml",
                "valkey.yaml",
                "meilisearch.yaml",
                "app.yaml",
                "admin-panel.yaml",
            ],
        )
        self.assertEqual(
            re.findall(r"(?m)^  - ([a-z0-9-]+\.yaml)$", rag),
            ["rag-api.yaml"],
        )

    def test_consumers_depend_on_operations_postgresql(self) -> None:
        for relative_path in ("core/app.yaml", "rag/rag-api.yaml"):
            release = (ROOT / "releases/librechat" / relative_path).read_text(
                encoding="utf-8"
            )
            with self.subTest(release=relative_path):
                self.assertIn(
                    "- name: postgres-operations\n"
                    "      namespace: infra-postgres-operations",
                    release,
                )
                self.assertNotIn("frontend-librechat-ferretdb", release)
                self.assertNotIn("frontend-librechat-rag-postgresql", release)
                self.assertEqual(release.count("name: RetryOnFailure"), 2)

    def test_dedicated_database_chart_and_release_contracts_are_removed(self) -> None:
        for chart in ("documentdb-postgresql", "ferretdb", "rag-postgresql"):
            with self.subTest(chart=chart):
                self.assertFalse(
                    (ROOT / "charts/librechat" / chart / "Chart.yaml").exists()
                )

        for release in (
            "core/documentdb-postgresql.yaml",
            "core/ferretdb.yaml",
            "rag/rag-postgresql.yaml",
        ):
            with self.subTest(release=release):
                self.assertFalse((ROOT / "releases/librechat" / release).exists())


if __name__ == "__main__":
    unittest.main()
