"""Offline third-party chart license inventory tests."""

from __future__ import annotations

import hashlib
import tarfile
import unittest
from pathlib import Path

import yaml


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

ARCHIVE_COMPONENTS = {
    "charts/agentgateway/charts/agentgateway-1.4.1.tgz": {
        "agentgateway/Chart.yaml": ("agentgateway", "1.4.1"),
    },
    "charts/cert-manager/approver-policy/charts/cert-manager-approver-policy-v0.25.1.tgz": {
        "cert-manager-approver-policy/Chart.yaml": (
            "cert-manager-approver-policy",
            "v0.25.1",
        ),
    },
    "charts/cert-manager/controller/charts/cert-manager-v1.20.2.tgz": {
        "cert-manager/Chart.yaml": ("cert-manager", "v1.20.2"),
    },
    "charts/external-secrets/charts/external-secrets-2.9.0.tgz": {
        "external-secrets/Chart.yaml": ("external-secrets", "2.9.0"),
        "external-secrets/charts/bitwarden-sdk-server/Chart.yaml": (
            "bitwarden-sdk-server",
            "v0.6.0",
        ),
    },
    "charts/fluent-bit/charts/fluent-bit-0.48.9.tgz": {
        "fluent-bit/Chart.yaml": ("fluent-bit", "0.48.9"),
    },
    "charts/kube-prometheus-stack/charts/kube-prometheus-stack-72.4.0.tgz": {
        "kube-prometheus-stack/Chart.yaml": ("kube-prometheus-stack", "72.4.0"),
        "kube-prometheus-stack/charts/crds/Chart.yaml": ("crds", "0.0.0"),
        "kube-prometheus-stack/charts/grafana/Chart.yaml": ("grafana", "9.0.0"),
        "kube-prometheus-stack/charts/kube-state-metrics/Chart.yaml": (
            "kube-state-metrics",
            "5.33.1",
        ),
        "kube-prometheus-stack/charts/prometheus-node-exporter/Chart.yaml": (
            "prometheus-node-exporter",
            "4.46.0",
        ),
        "kube-prometheus-stack/charts/prometheus-windows-exporter/Chart.yaml": (
            "prometheus-windows-exporter",
            "0.10.0",
        ),
    },
    "charts/langfuse/charts/langfuse-1.5.34.tgz": {
        "langfuse/Chart.yaml": ("langfuse", "1.5.34"),
        "langfuse/charts/clickhouse/Chart.yaml": ("clickhouse", "8.0.5"),
        "langfuse/charts/clickhouse/charts/common/Chart.yaml": (
            "common",
            "2.30.0",
        ),
        "langfuse/charts/clickhouse/charts/zookeeper/Chart.yaml": (
            "zookeeper",
            "13.7.4",
        ),
        "langfuse/charts/clickhouse/charts/zookeeper/charts/common/Chart.yaml": (
            "common",
            "2.30.0",
        ),
        "langfuse/charts/common/Chart.yaml": ("common", "2.30.0"),
        "langfuse/charts/minio/Chart.yaml": ("minio", "14.10.5"),
        "langfuse/charts/minio/charts/common/Chart.yaml": ("common", "2.29.0"),
        "langfuse/charts/postgresql/Chart.yaml": ("postgresql", "16.4.9"),
        "langfuse/charts/postgresql/charts/common/Chart.yaml": (
            "common",
            "2.29.1",
        ),
        "langfuse/charts/valkey/Chart.yaml": ("valkey", "2.2.4"),
        "langfuse/charts/valkey/charts/common/Chart.yaml": ("common", "2.29.1"),
    },
    "charts/openbao/charts/openbao-0.29.1.tgz": {
        "openbao/Chart.yaml": ("openbao", "0.29.1"),
    },
    "charts/opensearch/charts/opensearch-2.27.0.tgz": {
        "opensearch/Chart.yaml": ("opensearch", "2.27.0"),
    },
    "charts/reloader/charts/reloader-2.2.16.tgz": {
        "reloader/Chart.yaml": ("reloader", "2.2.16"),
    },
    "charts/trust-manager/charts/trust-manager-v0.24.0.tgz": {
        "trust-manager/Chart.yaml": ("trust-manager", "v0.24.0"),
    },
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

    def test_notice_maps_every_archive_and_embedded_chart(self) -> None:
        notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
        actual_archives = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "charts").glob("**/charts/*.tgz")
        }
        self.assertEqual(actual_archives, set(ARCHIVE_COMPONENTS))

        for archive, expected_components in ARCHIVE_COMPONENTS.items():
            with self.subTest(archive=archive):
                self.assertIn(f"`{archive}`", notice)
                with tarfile.open(ROOT / archive, "r:gz") as package:
                    actual_components = {}
                    for member in package.getmembers():
                        if not member.name.endswith("/Chart.yaml"):
                            continue
                        extracted = package.extractfile(member)
                        assert extracted is not None
                        chart = yaml.safe_load(extracted)
                        actual_components[member.name] = (
                            str(chart["name"]),
                            str(chart["version"]),
                        )
                self.assertEqual(actual_components, expected_components)

                for component in expected_components:
                    if component.count("/") > 1:
                        self.assertIn(f"`{component.removesuffix('/Chart.yaml')}`", notice)

        for license_name in LICENSE_SHA256:
            self.assertIn(f"LICENSES/{license_name}", notice)


if __name__ == "__main__":
    unittest.main()
