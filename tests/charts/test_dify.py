"""Rendered database and lifecycle contracts for Dify workloads."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"
RESOURCE_VALUES = ROOT / "releases/shared/resources.yaml"


def run(command: list[str]) -> str:
    """Run a manifest renderer and return its output."""
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def render_chart(name: str) -> str:
    """Render one Dify chart with deterministic synthetic values."""
    return run(
        [
            "helm",
            "template",
            f"frontend-dify-{name}",
            str(ROOT / f"charts/dify/{name}"),
            "--namespace",
            "frontend-dify",
            "--values",
            str(LINT_VALUES),
            "--values",
            str(RESOURCE_VALUES),
        ]
    )


def render_releases() -> str:
    """Render the Dify HelmRelease package."""
    return run(
        [
            "kustomize",
            "build",
            "--load-restrictor",
            "LoadRestrictionsNone",
            str(ROOT / "releases/dify"),
        ]
    )


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


def probe(resource_manifest: str, name: str) -> str:
    """Return one container probe from a rendered workload."""
    match = re.search(
        rf"(?ms)^          {re.escape(name)}:\n(?P<body>(?:            .*\n)*)",
        resource_manifest,
    )
    if match is None:
        raise AssertionError(f"Missing {name}")
    return match.group(0)


class DifyLifecycleTests(unittest.TestCase):
    """Keep Dify startup and dependency behavior explicit."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifests = {
            name: render_chart(name)
            for name in ("api", "beat", "plugin-daemon", "redis", "worker")
        }
        cls.workloads = {
            "api": resource(
                cls.manifests["api"],
                "Deployment",
                "frontend-dify-api-deployment",
            ),
            "migration": resource(
                cls.manifests["api"],
                "Job",
                "frontend-dify-api-migration-job",
            ),
            "beat": resource(
                cls.manifests["beat"],
                "Deployment",
                "frontend-dify-beat-deployment",
            ),
            "plugin-daemon": resource(
                cls.manifests["plugin-daemon"],
                "Deployment",
                "frontend-dify-plugin-daemon-deployment",
            ),
            "redis": resource(
                cls.manifests["redis"],
                "StatefulSet",
                "frontend-dify-redis-stateful-set",
            ),
            "worker": resource(
                cls.manifests["worker"],
                "Deployment",
                "frontend-dify-worker-deployment",
            ),
        }
        cls.releases = render_releases()

    def assert_http_probe(
        self,
        workload: str,
        probe_name: str,
        path: str,
        period: int,
        timeout: int,
        failures: int,
    ) -> None:
        block = probe(self.workloads[workload], probe_name)
        self.assertIn(f"path: {path}", block)
        self.assertIn("port: http", block)
        self.assertIn(f"periodSeconds: {period}", block)
        self.assertIn(f"timeoutSeconds: {timeout}", block)
        self.assertIn(f"failureThreshold: {failures}", block)

    def assert_exec_probe(
        self,
        workload: str,
        probe_name: str,
        command: list[str],
        period: int,
        failures: int,
    ) -> None:
        block = probe(self.workloads[workload], probe_name)
        for argument in command:
            self.assertIn(f"- {argument}", block)
        self.assertIn(f"periodSeconds: {period}", block)
        self.assertIn("timeoutSeconds: 5", block)
        self.assertIn(f"failureThreshold: {failures}", block)

    def test_api_startup_waits_for_local_health(self) -> None:
        self.assert_http_probe("api", "startupProbe", "/health", 5, 5, 180)

    def test_plugin_daemon_has_local_lifecycle_probes(self) -> None:
        for probe_name, period, failures in (
            ("startupProbe", 5, 60),
            ("readinessProbe", 5, 3),
            ("livenessProbe", 10, 6),
        ):
            with self.subTest(probe=probe_name):
                self.assert_http_probe(
                    "plugin-daemon",
                    probe_name,
                    "/health/check",
                    period,
                    5,
                    failures,
                )

    def test_redis_uses_authenticated_functional_lifecycle_probes(self) -> None:
        redis = self.workloads["redis"]
        self.assertRegex(
            redis,
            r"(?s)name: REDISCLI_AUTH.*?secretKeyRef:.*?"
            r"name: frontend-dify-secret.*?key: REDIS_PASSWORD",
        )
        for probe_name, period, failures in (
            ("startupProbe", 5, 60),
            ("readinessProbe", 5, 3),
            ("livenessProbe", 10, 6),
        ):
            with self.subTest(probe=probe_name):
                self.assert_exec_probe(
                    "redis",
                    probe_name,
                    ["redis-cli", "-e", "-h", "127.0.0.1", "ping"],
                    period,
                    failures,
                )
        self.assertNotIn("tcpSocket:", redis)

    def test_database_consumers_use_postgres_operations(self) -> None:
        host = "postgres-operations.infra-postgres-operations.svc.cluster.local"
        for workload in ("api", "migration", "worker", "beat", "plugin-daemon"):
            with self.subTest(workload=workload):
                manifest = self.workloads[workload]
                self.assertRegex(
                    manifest,
                    rf"name: DB_HOST\n\s+value: \"{re.escape(host)}\"",
                )
                self.assertRegex(manifest, r'name: DB_PORT\n\s+value: "5432"')
                self.assertRegex(manifest, r'name: DB_USERNAME\n\s+value: "dify"')
                if workload != "plugin-daemon":
                    self.assertRegex(
                        manifest,
                        r"envFrom:\n\s+- secretRef:\n"
                        r"\s+name: frontend-dify-secret",
                    )

        self.assertRegex(
            self.workloads["plugin-daemon"],
            r"name: DB_PASSWORD\n\s+valueFrom:\n\s+secretKeyRef:\n"
            r"\s+name: frontend-dify-secret\n\s+key: DB_PASSWORD",
        )

        for workload in ("api", "migration", "worker", "beat"):
            with self.subTest(plaintext_scram=workload):
                self.assertRegex(
                    self.workloads[workload],
                    r"name: PGSSLMODE\n\s+value: disable",
                )
        self.assertRegex(
            self.workloads["plugin-daemon"],
            r"name: DB_SSL_MODE\n\s+value: disable",
        )
        self.assertRegex(
            self.workloads["plugin-daemon"],
            r'name: DB_DATABASE\n\s+value: "dify_plugin"',
        )
        for workload in ("api", "migration", "worker", "beat"):
            self.assertRegex(
                self.workloads[workload],
                r'name: DB_DATABASE\n\s+value: "dify"',
            )

    def test_api_and_worker_use_dedicated_pgvector_database(self) -> None:
        for workload in ("api", "worker"):
            with self.subTest(workload=workload):
                manifest = self.workloads[workload]
                self.assertRegex(manifest, r'name: VECTOR_STORE\n\s+value: "pgvector"')
                self.assertRegex(
                    manifest,
                    r'name: PGVECTOR_DATABASE\n\s+value: "dify_vector"',
                )
                self.assertRegex(
                    manifest,
                    r"name: PGVECTOR_PASSWORD\n\s+valueFrom:\n"
                    r"\s+secretKeyRef:\n\s+name: frontend-dify-secret\n"
                    r"\s+key: DB_PASSWORD",
                )
                self.assertNotIn("WEAVIATE", manifest)

    def test_database_egress_is_exact_and_uses_provider_pod_port(self) -> None:
        policies = (
            ("api", "frontend-dify-api-egress"),
            ("api", "frontend-dify-api-migration-egress"),
            ("worker", "frontend-dify-worker-egress"),
            ("beat", "frontend-dify-beat-egress"),
            ("plugin-daemon", "frontend-dify-plugin-daemon-egress"),
        )
        for chart, name in policies:
            with self.subTest(policy=name):
                policy = resource(self.manifests[chart], "NetworkPolicy", name)
                self.assertIn(
                    "kubernetes.io/metadata.name: infra-postgres-operations",
                    policy,
                )
                self.assertIn("app.kubernetes.io/name: postgres-operations", policy)
                self.assertIn("app.kubernetes.io/instance: postgres-operations", policy)
                self.assertRegex(
                    policy,
                    r"app.kubernetes.io/instance: postgres-operations\n"
                    r"\s+ports:\n\s+- port: 9712\n\s+protocol: TCP",
                )

        migration = resource(
            self.manifests["api"],
            "NetworkPolicy",
            "frontend-dify-api-migration-egress",
        )
        self.assertIn('"helm.sh/hook": pre-install,pre-upgrade', migration)
        self.assertIn('"helm.sh/hook-weight": "-10"', migration)

    def test_releases_depend_on_postgres_operations(self) -> None:
        expected_timeouts = {
            "frontend-dify-plugin-daemon": "10m",
            "frontend-dify-redis": "10m",
        }
        for name, timeout in expected_timeouts.items():
            with self.subTest(release=name):
                release = resource(self.releases, "HelmRelease", name)
                self.assertIn(f"  timeout: {timeout}", release)

        for name in (
            "frontend-dify-api",
            "frontend-dify-worker",
            "frontend-dify-beat",
            "frontend-dify-plugin-daemon",
        ):
            with self.subTest(release=name):
                release = resource(self.releases, "HelmRelease", name)
                self.assertIn(
                    "  - name: postgres-operations\n"
                    "    namespace: infra-postgres-operations",
                    release,
                )

        self.assertNotIn("frontend-dify-postgres", self.releases)
        self.assertNotIn("frontend-dify-weaviate", self.releases)

    def test_obsolete_database_charts_are_removed(self) -> None:
        self.assertFalse((ROOT / "charts/dify/postgres/Chart.yaml").exists())
        self.assertFalse((ROOT / "charts/dify/weaviate/Chart.yaml").exists())


if __name__ == "__main__":
    unittest.main()
