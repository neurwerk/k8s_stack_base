"""Static rollout contracts for Dify Secret consumers."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"
RESOURCE_VALUES = ROOT / "releases/shared/resources.yaml"
DIFY_SECRETS = {"frontend-dify-secret", "frontend-dify-openbao-secret"}
EXPECTED_WORKLOAD_CONSUMERS = {
    ("Deployment", "frontend-dify-api-deployment"): DIFY_SECRETS,
    ("Deployment", "frontend-dify-beat-deployment"): {"frontend-dify-secret"},
    ("Deployment", "frontend-dify-plugin-daemon-deployment"): {
        "frontend-dify-secret"
    },
    ("Deployment", "frontend-dify-sandbox-deployment"): {"frontend-dify-secret"},
    ("Deployment", "frontend-dify-worker-deployment"): {"frontend-dify-secret"},
    ("StatefulSet", "frontend-dify-redis-stateful-set"): {"frontend-dify-secret"},
}


def render(chart: Path) -> str:
    """Render one Dify chart with deterministic synthetic values."""
    result = subprocess.run(
        [
            "helm",
            "template",
            chart.name,
            str(chart),
            "--namespace",
            "frontend-dify",
            "--values",
            str(LINT_VALUES),
            "--values",
            str(RESOURCE_VALUES),
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


def identity(document: str) -> tuple[str, str]:
    """Return the kind and metadata name of a rendered resource."""
    kind = re.search(r"(?m)^kind: (\S+)$", document)
    name = re.search(r"(?m)^metadata:\n(?:.*\n)*?  name: (\S+)$", document)
    if kind is None or name is None:
        raise AssertionError(f"Missing resource identity:\n{document}")
    return kind.group(1), name.group(1)


def referenced_dify_secrets(document: str) -> set[str]:
    """Return Dify Secrets directly referenced by a pod spec."""
    names = re.findall(
        r"(?:secretKeyRef|secretRef):\n\s+name: (frontend-dify-(?:openbao-)?secret)",
        document,
    )
    names.extend(
        re.findall(
            r"secret:\n(?:\s+.*\n)*?\s+secretName: (frontend-dify-(?:openbao-)?secret)",
            document,
        )
    )
    return set(names)


class DifyRolloutTests(unittest.TestCase):
    """Keep Dify Secret rollout and startup behavior explicit."""

    @classmethod
    def setUpClass(cls) -> None:
        charts = sorted((ROOT / "charts/dify").glob("*/Chart.yaml"))
        cls.manifests = {chart.parent.name: render(chart.parent) for chart in charts}
        cls.beat_manifest = cls.manifests["beat"]

    def test_every_long_running_consumer_has_one_exact_reload_trigger(self) -> None:
        consumers: dict[tuple[str, str], set[str]] = {}
        resources: dict[tuple[str, str], str] = {}
        for manifest in self.manifests.values():
            for document in documents(manifest):
                resource_identity = identity(document)
                if resource_identity[0] not in {"Deployment", "StatefulSet"}:
                    continue
                secrets = referenced_dify_secrets(document)
                if secrets:
                    consumers[resource_identity] = secrets
                    resources[resource_identity] = document

        self.assertEqual(consumers, EXPECTED_WORKLOAD_CONSUMERS)
        for resource_identity, secrets in consumers.items():
            with self.subTest(resource=resource_identity):
                metadata = resources[resource_identity].split("\nspec:", maxsplit=1)[0]
                annotations = re.findall(
                    r"secret\.reloader\.stakater\.com/reload: \"([^\"]+)\"",
                    metadata,
                )
                self.assertEqual(len(annotations), 1)
                annotated_secrets = [name.strip() for name in annotations[0].split(",")]
                self.assertEqual(len(annotated_secrets), len(set(annotated_secrets)))
                self.assertEqual(set(annotated_secrets), secrets)
                self.assertNotIn("frontend-dify-runtime-secret", metadata)
                self.assertNotIn("checksum/secret", resources[resource_identity])

    def test_migration_job_uses_current_secret_without_reloader_annotation(self) -> None:
        job_consumers = {}
        for manifest in self.manifests.values():
            for document in documents(manifest):
                resource_identity = identity(document)
                if resource_identity[0] != "Job":
                    continue
                secrets = referenced_dify_secrets(document)
                if secrets:
                    job_consumers[resource_identity] = (secrets, document)

        self.assertEqual(
            {name: secrets for name, (secrets, _) in job_consumers.items()},
            {("Job", "frontend-dify-api-migration-job"): {"frontend-dify-secret"}},
        )
        migration = job_consumers[("Job", "frontend-dify-api-migration-job")][1]
        self.assertIn('"helm.sh/hook": pre-install,pre-upgrade', migration)
        self.assertNotIn("reloader.stakater.com", migration)

    def test_only_shared_release_imports_runtime_secret_values(self) -> None:
        consumers = sorted(
            path.name
            for path in (ROOT / "releases/dify").glob("*.yaml")
            if "name: frontend-dify-runtime-secret" in path.read_text(encoding="utf-8")
        )
        self.assertEqual(consumers, ["shared.yaml"])

    def test_beat_is_singleton_without_api_storage(self) -> None:
        self.assertIn("strategy:\n    type: Recreate", self.beat_manifest)
        self.assertIn("rollingUpdate: null", self.beat_manifest)
        self.assertNotIn("persistentVolumeClaim:", self.beat_manifest)
        self.assertNotIn("frontend-dify-api-pvc", self.beat_manifest)

    def test_beat_memory_limit_is_at_least_one_gibibyte(self) -> None:
        match = re.search(r"(?s)limits:.*?memory: (\d+)(Ki|Mi|Gi)\b", self.beat_manifest)
        self.assertIsNotNone(match)
        assert match is not None
        factors = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}
        self.assertGreaterEqual(int(match.group(1)) * factors[match.group(2)], 1024**3)

    def test_api_waits_for_oidc_and_guardrail_providers(self) -> None:
        release = (ROOT / "releases/dify/api.yaml").read_text(encoding="utf-8")
        self.assertIn("timeout: 60m", release)
        self.assertIn("- name: keycloak-dify-oidc\n      namespace: auth-keycloak", release)
        self.assertIn(
            "- name: agentgateway\n      namespace: infra-agentgateway",
            release,
        )
        self.assertIn(
            "- name: agentgateway-extproc\n      namespace: monitor-agentgateway-extproc",
            release,
        )
        self.assertNotIn("keycloak-dify-api-key", release)

    def test_model_setup_has_bounded_database_lock_wait(self) -> None:
        api_manifest = self.manifests["api"]
        self.assertIn("name: DIFY_BOOTSTRAP_LOCK_TIMEOUT_SECONDS", api_manifest)
        self.assertIn('value: "1800"', api_manifest)


if __name__ == "__main__":
    unittest.main()
