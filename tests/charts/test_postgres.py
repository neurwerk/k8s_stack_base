"""Rendered contracts for the shared PostgreSQL charts."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"


def render(component: str, namespace: str) -> str:
    """Render one PostgreSQL chart with synthetic non-secret values."""
    result = subprocess.run(
        [
            "helm",
            "template",
            f"postgres-{component}",
            str(ROOT / f"charts/postgres/{component}"),
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


def resource(manifest: str, kind: str, name: str) -> str:
    """Return exactly one rendered resource by kind and metadata name."""
    matches = [
        document
        for document in re.split(r"(?m)^---\s*$", manifest)
        if f"kind: {kind}\n" in document
        and re.search(rf"(?m)^  name: {re.escape(name)}$", document)
    ]
    if len(matches) != 1:
        raise AssertionError(f"Expected one {kind} {name}, found {len(matches)}")
    return matches[0]


class PostgresChartTests(unittest.TestCase):
    """Keep persistence, transport, and provisioning boundaries explicit."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.auth = render("auth", "infra-postgres-auth")
        cls.operations = render("operations", "infra-postgres-operations")

    def test_auth_postgresql_requires_verified_tls_for_tcp(self) -> None:
        config = resource(self.auth, "ConfigMap", "postgres-auth-config")
        stateful_set = resource(self.auth, "StatefulSet", "postgres-auth")
        certificate = resource(self.auth, "Certificate", "postgres-auth-server")

        self.assertIn("hostssl all all 0.0.0.0/0 scram-sha-256", config)
        self.assertIn("hostnossl all all 0.0.0.0/0 reject", config)
        self.assertIn("ssl_min_protocol_version=TLSv1.3", stateful_set)
        self.assertIn("postgres-auth.infra-postgres-auth.svc.cluster.local", certificate)

    def test_provisioning_imports_passwords_without_process_arguments(self) -> None:
        auth_job = resource(self.auth, "Job", "postgres-auth-provision")
        operations_job = resource(
            self.operations, "Job", "postgres-operations-provision"
        )
        operations_bootstrap = resource(
            self.operations, "ConfigMap", "postgres-operations-bootstrap"
        )

        for manifest in (auth_job, operations_job, operations_bootstrap):
            with self.subTest(resource=manifest.splitlines()[0:8]):
                self.assertIn("\\getenv", manifest)
                self.assertNotRegex(manifest, r"--set=[^\s]*password")
                self.assertIn("SET log_statement = 'none'", manifest)
                self.assertIn("SET log_min_error_statement = 'panic'", manifest)

    def test_database_access_is_explicitly_partitioned(self) -> None:
        auth_job = resource(self.auth, "Job", "postgres-auth-provision")
        operations_job = resource(
            self.operations, "Job", "postgres-operations-provision"
        )

        self.assertIn(
            "REVOKE CONNECT, TEMPORARY ON DATABASE postgres, template1 FROM PUBLIC",
            auth_job,
        )
        self.assertIn("REVOKE ALL ON DATABASE keycloak FROM PUBLIC", auth_job)
        self.assertIn(
            "REVOKE CONNECT, TEMPORARY ON DATABASE postgres, template1 FROM PUBLIC",
            operations_job,
        )
        self.assertIn(
            "REVOKE ALL ON DATABASE dify, dify_plugin, dify_vector, postgres_langfuse, librechat_rag FROM PUBLIC",
            operations_job,
        )

    def test_operations_transport_matches_accepted_canary_exception(self) -> None:
        stateful_set = resource(
            self.operations, "StatefulSet", "postgres-operations"
        )
        job = resource(self.operations, "Job", "postgres-operations-provision")
        certificate = resource(
            self.operations,
            "Certificate",
            "postgres-operations-documentdb",
        )

        self.assertIn('name: TLS_MODE\n              value: requireTLS', stateful_set)
        self.assertIn('name: ALLOW_EXTERNAL_CONNECTIONS\n              value: "true"', stateful_set)
        self.assertIn(
            "name: DATA_PATH\n              value: /data/postgresql", stateful_set
        )
        self.assertIn('name: PGSSLMODE\n              value: disable', job)
        self.assertIn(
            "postgres-operations.infra-postgres-operations.svc.cluster.local",
            certificate,
        )
        self.assertIn(
            'ignore-check.kube-linter.io/privilege-escalation-container: "The reviewed upstream DocumentDB local image requires passwordless sudo for startup directory preparation."',
            stateful_set,
        )
        self.assertIn("allowPrivilegeEscalation: true", stateful_set)

    def test_documentdb_runtime_user_is_not_the_bootstrap_administrator(self) -> None:
        stateful_set = resource(
            self.operations, "StatefulSet", "postgres-operations"
        )
        config = resource(
            self.operations, "ConfigMap", "postgres-operations-bootstrap"
        )
        job = resource(self.operations, "Job", "postgres-operations-provision")

        self.assertIn(
            "name: USERNAME\n              value: operations_admin",
            stateful_set,
        )
        self.assertNotIn("documentdb_admin", stateful_set)
        self.assertNotIn("value: documentdb_admin", job)
        self.assertIn('{ role: "readWriteAnyDatabase", db: "admin" }', config)
        self.assertIn('{ role: "clusterAdmin", db: "admin" }', config)
        self.assertIn("admin.createUser", config)
        self.assertIn(
            "admin.updateUser(username, { pwd: specification.pwd })", config
        )
        self.assertIn("admin.runCommand({ usersInfo: 1 })", config)
        self.assertIn("Array.isArray(result.users)", config)
        self.assertNotIn("admin.getUser", config)
        self.assertNotIn("target.createUser", config)
        self.assertNotIn("target.updateUser", config)
        self.assertIn("configured.db !== \"admin\"", config)
        self.assertIn('role.role === "readWriteAnyDatabase"', config)
        self.assertIn('role.role === "clusterAdmin"', config)
        self.assertIn('role.db === "admin"', config)
        self.assertIn("roles.length === 2", config)
        self.assertIn("--file=/opt/neurwerk/provision-documentdb.js", job)
        self.assertIn("--file=/opt/neurwerk/verify-documentdb.js", job)
        self.assertIn("ALTER ROLE operations_admin CREATEROLE", job)
        self.assertIn(
            "GRANT documentdb_admin_role TO operations_admin WITH ADMIN OPTION",
            job,
        )
        self.assertIn("trap cleanup_documentdb_authorization_on_exit 0", job)
        self.assertIn(
            "GRANT documentdb_admin_role TO librechat", job
        )
        self.assertIn(
            "REVOKE ADMIN OPTION FOR documentdb_admin_role FROM operations_admin CASCADE",
            job,
        )
        self.assertIn("ALTER ROLE operations_admin NOCREATEROLE", job)
        self.assertIn("trap - 0", job)
        self.assertIn("operations_admin' AND rolcreaterole", job)
        self.assertIn("parent.rolname = 'documentdb_admin_role'", job)
        self.assertIn("AND NOT membership.admin_option", job)
        self.assertIn("child.rolname = 'operations_admin'", job)
        self.assertIn(
            "DocumentDB administrator role delegation revocation failed", job
        )
        self.assertIn("grantor.rolname = 'documentdb'", job)
        self.assertIn(
            "DocumentDB application role ownership verification failed", job
        )
        self.assertIn(
            "for database in dify dify_plugin dify_vector postgres_langfuse librechat_rag",
            job,
        )
        self.assertIn(
            'PGPASSWORD="$DOCUMENTDB_PASSWORD" PGUSER=librechat PGDATABASE=postgres',
            job,
        )
        self.assertIn(
            'PGPASSWORD="$DOCUMENTDB_PASSWORD" PGUSER=librechat PGDATABASE="$database"',
            job,
        )
        self.assertIn(
            "DocumentDB application role unexpectedly connected to PostgreSQL database",
            job,
        )
        self.assertIn("DocumentDB application user authentication failed", config)
        self.assertIn("__neurwerk_provisioning_verification", config)
        self.assertIn("collection.replaceOne", config)
        self.assertIn("collection.findOne", config)
        self.assertIn("collection.deleteOne", config)
        self.assertIn("collection.drop", config)
        self.assertIn("name: DOCUMENTDB_USER\n              value: librechat", job)
        self.assertNotIn("--password", job)

    def test_operations_ingress_selects_each_consumer_exactly(self) -> None:
        policy = resource(
            self.operations, "NetworkPolicy", "postgres-operations-ingress"
        )
        expected_identities = {
            "api": "frontend-dify-api",
            "api-migration": "frontend-dify-api",
            "worker": "frontend-dify-worker",
            "beat": "frontend-dify-beat",
            "plugin-daemon": "frontend-dify-plugin-daemon",
            "frontend-librechat-rag-api": "frontend-librechat-rag-api",
            "frontend-librechat": "frontend-librechat",
        }
        for name, instance in expected_identities.items():
            with self.subTest(name=name):
                self.assertRegex(
                    policy,
                    rf"app\.kubernetes\.io/name: {re.escape(name)}\n"
                    rf"\s+app\.kubernetes\.io/instance: {re.escape(instance)}",
                )

        self.assertIn("app.kubernetes.io/component: web", policy)
        self.assertIn("app.kubernetes.io/component: worker", policy)
        self.assertIn("app.kubernetes.io/name: langfuse-retention-provisioning", policy)
        self.assertNotIn("app.kubernetes.io/part-of: frontend-dify", policy)

    def test_stateful_storage_is_retained(self) -> None:
        for manifest, name in (
            (self.auth, "postgres-auth"),
            (self.operations, "postgres-operations"),
        ):
            stateful_set = resource(manifest, "StatefulSet", name)
            self.assertIn("whenDeleted: Retain", stateful_set)
            self.assertIn("whenScaled: Retain", stateful_set)
            self.assertIn('storageClassName: "infra-rook-ceph-rbd"', stateful_set)

    def test_provisioning_jobs_are_bounded_release_gates(self) -> None:
        for manifest, name in (
            (self.auth, "postgres-auth-provision"),
            (self.operations, "postgres-operations-provision"),
        ):
            job = resource(manifest, "Job", name)
            self.assertIn('"helm.sh/hook": post-install,post-upgrade', job)
            self.assertIn("activeDeadlineSeconds: 300", job)
            self.assertIn("restartPolicy: Never", job)
            self.assertNotIn("restartPolicy: OnFailure", job)
            self.assertIn("deadline=$(($(date +%s) + 180))", job)
            self.assertIn("current_time=$(date +%s)", job)
            self.assertNotIn("$SECONDS", job)
            self.assertIn("readOnlyRootFilesystem: true", job)
            self.assertIn("automountServiceAccountToken: false", job)


if __name__ == "__main__":
    unittest.main()
