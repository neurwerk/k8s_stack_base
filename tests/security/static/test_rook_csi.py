"""Verify the Rook chart uses only the Ceph-CSI operator integration."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


REPOSITORY_ROOT: Path = Path(__file__).resolve().parents[3]
ROOK_CHART: Path = REPOSITORY_ROOT / "charts/rook-ceph"


class RookCsiManifestTests(unittest.TestCase):
    """Assert the rendered Rook CSI operator contract."""

    @classmethod
    def setUpClass(cls) -> None:
        """Render the chart once with synthetic required storage values."""
        result = subprocess.run(
            [
                "helm",
                "template",
                "rook-ceph",
                str(ROOK_CHART),
                "--namespace",
                "infra-rook-ceph",
                "--set",
                "infraRookCeph.storage.nodeName=test-node",
                "--set",
                "infraRookCeph.storage.devicePath=/dev/test",
                "--set",
                "infraRookCeph.objectStore.publicHostname=objects.test.example",
                "--set",
                "infraRookCeph.objectStore.externalGateway.enabled=true",
                "--set",
                "publicCertificates.useProduction=true",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Helm render failed:\n{result.stderr}{result.stdout}")
        cls.manifest = result.stdout
        cls.documents = tuple(document for document in cls.manifest.split("\n---\n") if document)

    def test_object_gateway_exposes_private_buckets_through_https(self) -> None:
        """Provide browser-reachable hosts for authenticated presigned URLs."""
        gateway = self._document("Gateway", "infra-rook-ceph-object-gateway")
        route = self._document("HTTPRoute", "infra-rook-ceph-object-gateway")
        self.assertIn('hostname: "objects.test.example"', gateway)
        self.assertIn(
            'cert-manager.io/cluster-issuer: "letsencrypt-production-cluster-issuer"',
            gateway,
        )
        self.assertIn('name: infra-rook-ceph-object-gateway-tls', gateway)
        self.assertIn('name: rook-ceph-rgw-infra-rook-ceph-object-store', route)
        self.assertIn('port: 80', route)

    def test_operator_uses_csi_operator_and_generates_connection_profiles(self) -> None:
        """Require Rook's CSI operator mode without manually duplicated generated objects."""
        operator_config = self._document("ConfigMap", "rook-ceph-operator-config")
        self.assertIn('ROOK_USE_CSI_OPERATOR: "true"', operator_config)
        self.assertNotIn("kind: CephConnection", self.manifest)
        self.assertNotIn("kind: ClientProfile", self.manifest)

    def test_both_csi_operator_drivers_render_once(self) -> None:
        """Require exactly one RBD and one CephFS CSI operator Driver."""
        drivers = [document for document in self.documents if "\nkind: Driver\n" in document]
        names = {
            self._resource_name(document)
            for document in drivers
        }
        self.assertEqual(
            names,
            {"rook-ceph.rbd.csi.ceph.com", "rook-ceph.cephfs.csi.ceph.com"},
        )
        self.assertEqual(len(drivers), 2)
        self.assertNotIn("kind: CSIDriver", self.manifest)

    def test_csi_operator_health_port_is_declared(self) -> None:
        """Require health probes and the named container port on port 8081."""
        deployment = self._document("Deployment", "ceph-csi-controller-manager")
        self.assertEqual(len(re.findall(r"port: 8081", deployment)), 2)
        self.assertIn("containerPort: 8081", deployment)

    def test_rbd_storage_and_snapshot_contract_renders(self) -> None:
        """Require explicit single-node RBD provisioning and snapshot support."""
        pool = self._document("CephBlockPool", "infra-rook-ceph-rbd")
        self.assertIn('size: 1', pool)
        self.assertIn('min_size: "1"', pool)

        storage_class = self._document("StorageClass", "infra-rook-ceph-rbd")
        self.assertIn("provisioner: rook-ceph.rbd.csi.ceph.com", storage_class)
        self.assertIn("provisioner-secret-name: rook-csi-rbd-provisioner", storage_class)
        self.assertIn("node-stage-secret-name: rook-csi-rbd-node", storage_class)
        self.assertIn('csi.storage.k8s.io/fstype: ext4', storage_class)

        openbao_storage_class = self._document(
            "StorageClass", "infra-rook-ceph-rbd-openbao"
        )
        self.assertIn("provisioner: rook-ceph.rbd.csi.ceph.com", openbao_storage_class)
        self.assertIn("reclaimPolicy: Retain", openbao_storage_class)
        self.assertIn("allowVolumeExpansion: true", openbao_storage_class)
        self.assertIn("volumeBindingMode: WaitForFirstConsumer", openbao_storage_class)
        self.assertIn('pool: "infra-rook-ceph-rbd"', openbao_storage_class)
        self.assertIn("provisioner-secret-name: rook-csi-rbd-provisioner", openbao_storage_class)
        self.assertIn("node-stage-secret-name: rook-csi-rbd-node", openbao_storage_class)
        self.assertIn('csi.storage.k8s.io/fstype: ext4', openbao_storage_class)

        snapshot_class = self._document(
            "VolumeSnapshotClass", "infra-rook-ceph-rbd-snapshots"
        )
        self.assertIn("driver: rook-ceph.rbd.csi.ceph.com", snapshot_class)
        self.assertIn("deletionPolicy: Delete", snapshot_class)

        controller = self._document("Deployment", "snapshot-controller")
        self.assertIn(
            "registry.k8s.io/sig-storage/snapshot-controller:v8.5.0", controller
        )

        self.assertNotIn('storageclass.kubernetes.io/is-default-class: "true"', self.manifest)
        self.assertIn("snapshotPolicy: volumeSnapshot", self.manifest)

    def test_readiness_checks_storage_and_pii_object_resources(self) -> None:
        """Gate dependent releases on all Rook resources consumed by the stack."""
        bucket_storage_class = self._document(
            "StorageClass", "infra-rook-ceph-object-bucket"
        )
        self.assertIn("provisioner: ceph.rook.io/bucket", bucket_storage_class)
        self.assertIn("reclaimPolicy: Retain", bucket_storage_class)
        self.assertNotIn("infra-rook-ceph.ceph.rook.io/bucket", bucket_storage_class)

        job = self._document("Job", "infra-rook-ceph-readiness-job")
        self.assertIn('[ "$pool_phase" = "Ready" ]', job)
        self.assertIn('[ "$obc_phase" = "Bound" ]', job)
        self.assertIn('[ "$publisher_phase" = "Ready" ]', job)
        self.assertIn('[ "$model_sync_phase" = "Ready" ]', job)
        self.assertIn(".data.AccessKey", job)
        self.assertIn(".data.SecretKey", job)
        self.assertIn(".data.Endpoint", job)
        self.assertIn('rook-ceph-object-user-infra-rook-ceph-object-store-pii-model-sync', job)
        self.assertIn("--write-out '%{http_code}'", job)
        self.assertIn('[ "$rgw_status" = "200" ] || [ "$rgw_status" = "403" ]', job)
        self.assertIn("$RGW_ENDPOINT/", job)
        self.assertIn(
            "http://rook-ceph-rgw-infra-rook-ceph-object-store.infra-rook-ceph.svc:80",
            job,
        )

    def test_readiness_runs_cleanup_safe_rbd_persistence_smoke_test(self) -> None:
        """Exercise dynamic RBD provisioning without leaving hook resources behind."""
        job = self._document("Job", "infra-rook-ceph-readiness-job")
        self.assertIn("trap cleanup_smoke_test EXIT", job)
        self.assertIn("trap 'exit 1' INT TERM", job)
        self.assertIn("kind: PersistentVolumeClaim", job)
        self.assertIn("storageClassName: $RBD_STORAGE_CLASS", job)
        self.assertIn("infra-rook-ceph-readiness-writer-pod", job)
        self.assertIn("infra-rook-ceph-readiness-reader-pod", job)
        self.assertIn('test "\\$(cat /data/readiness-token)" = "\\$SMOKE_TOKEN"', job)

    def test_rgw_consumers_use_the_actual_service_name(self) -> None:
        endpoint = (
            "rook-ceph-rgw-infra-rook-ceph-object-store."
            "infra-rook-ceph.svc.cluster.local:80"
        )
        for values in (
            REPOSITORY_ROOT / "charts/langfuse/values.yaml",
            REPOSITORY_ROOT / "charts/opensearch/values.yaml",
        ):
            self.assertIn(endpoint, values.read_text(encoding="utf-8"), str(values))

    def test_helm_timeout_exceeds_readiness_job_deadline(self) -> None:
        """Allow Flux enough overhead to observe the readiness hook's full deadline."""
        job = self._document("Job", "infra-rook-ceph-readiness-job")
        release = (REPOSITORY_ROOT / "releases/rook-ceph/app.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("activeDeadlineSeconds: 2700", job)
        self.assertIn("timeout: 50m", release)

    def test_readiness_rbac_is_resource_and_namespace_scoped(self) -> None:
        """Allow only named readiness resources, including model-sync cross-namespace data."""
        local_role = self._document("Role", "infra-rook-ceph-readiness-role", occurrence=1)
        model_sync_role = self._document("Role", "infra-rook-ceph-readiness-role", occurrence=2)
        self.assertNotIn('verbs: ["get", "list", "watch"]', local_role)
        self.assertIn('resources: ["cephblockpools"]', local_role)
        self.assertIn('resources: ["objectbucketclaims"]', local_role)
        self.assertIn('resources: ["persistentvolumeclaims"]', local_role)
        self.assertIn('resources: ["pods"]', local_role)
        self.assertIn('verbs: ["create"]', local_role)
        self.assertIn('verbs: ["get", "delete"]', local_role)
        self.assertIn("namespace: monitor-pii-engine", model_sync_role)
        self.assertIn('resources: ["cephobjectstoreusers"]', model_sync_role)
        self.assertIn('resources: ["secrets"]', model_sync_role)
        self.assertIn('verbs: ["get"]', model_sync_role)

    def _document(self, kind: str, name: str, occurrence: int = 1) -> str:
        """Return a rendered document by kind and metadata name."""
        matches = 0
        for document in self.documents:
            if re.search(rf"(?m)^kind: {re.escape(kind)}$", document) and re.search(
                rf"(?m)^  name: {re.escape(name)}$", document
            ):
                matches += 1
                if matches == occurrence:
                    return document
        self.fail(f"Missing occurrence {occurrence} of {kind}/{name}")

    @staticmethod
    def _resource_name(document: str) -> str:
        """Extract a resource metadata name from a rendered document."""
        match = re.search(r"(?m)^  name: ([^\n]+)$", document)
        if match is None:
            raise AssertionError(f"Rendered resource has no metadata name:\n{document}")
        return match.group(1)


if __name__ == "__main__":
    unittest.main()
