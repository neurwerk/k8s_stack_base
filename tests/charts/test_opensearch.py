"""Rendered contracts for the OpenSearch chart."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLAIM_NAME = "monitor-opensearch-archive-object-bucket-claim"
KEYSTORE_VOLUME_NAME = f"keystore-{CLAIM_NAME}"


def render() -> str:
    """Render OpenSearch with the platform validation values."""
    result = subprocess.run(
        [
            "helm",
            "template",
            "monitor-opensearch",
            str(ROOT / "charts/opensearch"),
            "--namespace",
            "monitor-opensearch",
            "--values",
            str(ROOT / "tests/validation/helm-lint-values.yaml"),
            "--values",
            str(ROOT / "releases/shared/resources.yaml"),
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


class OpenSearchChartTests(unittest.TestCase):
    """Keep archive and security bootstrap contracts explicit."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = render()

    def test_archive_claim_produces_a_valid_keystore_volume_name(self) -> None:
        claim = resource(self.manifest, "ObjectBucketClaim", CLAIM_NAME)
        stateful_set = resource(
            self.manifest, "StatefulSet", "opensearch-cluster-master"
        )

        self.assertIn("bucketName: opensearch-archive", claim)
        self.assertLessEqual(len(KEYSTORE_VOLUME_NAME), 63)
        self.assertIn(f"secretName: {CLAIM_NAME}", stateful_set)
        self.assertEqual(
            stateful_set.count(f"name: {KEYSTORE_VOLUME_NAME}"),
            2,
        )

    def test_security_bootstrap_applies_the_complete_managed_user_set(self) -> None:
        config = resource(
            self.manifest,
            "ConfigMap",
            "monitor-opensearch-security-config",
        )
        job = resource(
            self.manifest,
            "Job",
            "monitor-opensearch-init-security-bootstrap-job",
        )

        expected_users = {
            "admin": ("ADMIN_HASH_PLACEHOLDER", "adminPassword"),
            "fluent-bit-ingest": (
                "FLUENT_BIT_HASH_PLACEHOLDER",
                "fluentBitPassword",
            ),
            "studio-logs-read": ("STUDIO_HASH_PLACEHOLDER", "studioPassword"),
        }
        for user, (placeholder, secret_key) in expected_users.items():
            with self.subTest(user=user):
                self.assertIn(user, config)
                self.assertIn(placeholder, config)
                self.assertIn(placeholder, job)
                self.assertRegex(
                    job,
                    rf"secretKeyRef:\n\s+name: monitor-opensearch-secret\n\s+key: {secret_key}",
                )

        self.assertIn('grep -q "_HASH_PLACEHOLDER"', job)


if __name__ == "__main__":
    unittest.main()
