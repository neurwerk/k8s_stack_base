"""Offline third-party license integrity tests."""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

LICENSE_SHA256 = {
    "AgentGateway-ATTRIBUTION.txt": "b10cf83a664fa25b516fa75e39e69b48103a62ae4704e21ef377078549fd50eb",
    "Apache-2.0.txt": "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    "Bitnami-Charts-2024-ATTRIBUTION.txt": "e5708fc7aaad535f658719951e8aa4479c82d917836da379c06338c816dd99d8",
    "Bitnami-Charts-2025-ATTRIBUTION.txt": "17679d6d7d4c616e6d67a5c331e87745a61178632e0635a47e49a596f7dc1ca1",
    "Grafana-Helm-Charts-ATTRIBUTION.txt": "7bd32658cac5d8328f8c6732107ff4968d6e53e648e8ea5bbdfe58b8ea535eb6",
    "Langfuse-MIT.txt": "4a901786b1092b29ed1fdad54cb4efc94856015e8477261345821676a6033697",
    "OpenBao-MPL-2.0.txt": "a370fb74ebae555472e6c27650ef446fbd82b87fcba8416c58a10184f5ef5289",
    "OpenSearch-Helm-Charts-NOTICE.txt": "a035b0586e1d9c455796ff13a96ca78f2917c861c76792784eab59e5eeb63c09",
}


class ThirdPartyLicenseTest(unittest.TestCase):
    def test_offline_license_texts_are_exact(self) -> None:
        license_dir = ROOT / "LICENSES"
        actual_files = {path.name for path in license_dir.iterdir() if path.is_file()}
        self.assertEqual(actual_files, set(LICENSE_SHA256))

        for name, expected in LICENSE_SHA256.items():
            with self.subTest(name=name):
                digest = hashlib.sha256((license_dir / name).read_bytes()).hexdigest()
                self.assertEqual(digest, expected)


if __name__ == "__main__":
    unittest.main()
