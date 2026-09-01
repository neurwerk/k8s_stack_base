"""Static checks for the PII engine Kubernetes integration."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "charts/pii-engine"
MODEL_SYNC_CHART = ROOT / "charts/pii-engine-model-sync"
AGENTGATEWAY_CHART = ROOT / "charts/agentgateway"
AGENTGATEWAY_EXTPROC_CHART = ROOT / "charts/agentgateway-extproc"
AGENTGATEWAY_RELEASE = ROOT / "releases/agentgateway/app.yaml"
MODEL_SYNC_RELEASE = ROOT / "releases/pii-engine-model-sync/app.yaml"
RUNTIME_RELEASE = ROOT / "releases/pii-engine/app.yaml"


class PiiEngineIntegrationTests(unittest.TestCase):
    """Require the engine chart's security and rollout boundaries."""

    @classmethod
    def setUpClass(cls) -> None:
        manifests = {}
        for release, chart in (
            ("pii-engine", CHART),
            ("pii-engine-model-sync", MODEL_SYNC_CHART),
        ):
            result = subprocess.run(
                [
                    "helm",
                    "template",
                    release,
                    str(chart),
                    "--namespace",
                    "monitor-pii-engine",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr)
            manifests[release] = result.stdout
        cls.runtime_manifest = manifests["pii-engine"]
        cls.sync_manifest = manifests["pii-engine-model-sync"]
        cls.manifest = "\n".join(manifests.values())
        extproc = subprocess.run(
            [
                "helm",
                "template",
                "agentgateway-extproc",
                str(AGENTGATEWAY_EXTPROC_CHART),
                "--namespace",
                "monitor-agentgateway-extproc",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if extproc.returncode != 0:
            raise RuntimeError(extproc.stderr)
        cls.extproc_manifest = extproc.stdout
        gpu = subprocess.run(
            [
                "helm",
                "template",
                "pii-engine",
                str(CHART),
                "--namespace",
                "monitor-pii-engine",
                "--set",
                "monitorPiiEngine.image=ghcr.io/neurwerk/k8s-stack-pii-engine:test-cu124",
                "--set",
                "monitorPiiEngine.device=cuda:0",
                "--set",
                "monitorPiiEngine.accelerator.enabled=true",
                "--set",
                "monitorPiiEngine.nodeSelector.accelerator=nvidia",
                "--set",
                "monitorPiiEngine.tolerations[0].key=nvidia.com/gpu",
                "--set",
                "monitorPiiEngine.tolerations[0].operator=Exists",
                "--set",
                "monitorPiiEngine.tolerations[0].effect=NoSchedule",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if gpu.returncode != 0:
            raise RuntimeError(gpu.stderr)
        cls.gpu_manifest = gpu.stdout
        rook = subprocess.run(
            [
                "helm",
                "template",
                "rook-ceph",
                str(ROOT / "charts/rook-ceph"),
                "--namespace",
                "infra-rook-ceph",
                "--set",
                "infraRookCeph.storage.nodeName=test-node",
                "--set",
                "infraRookCeph.storage.devicePath=/dev/test",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if rook.returncode != 0:
            raise RuntimeError(rook.stderr)
        cls.rook_manifest = rook.stdout
        cls.temp_directory = tempfile.TemporaryDirectory(prefix="pii-routing-")
        fixture = Path(cls.temp_directory.name) / "values.yaml"
        fixture.write_text(
            """authKeycloak:
  hostname: auth.routing.test
  realm: routing
  agentgatewayClientRoles:
    - llm:invoke
    - model:remote/default:invoke
    - model:remote/code:invoke
    - model:local/sensitive:invoke
infraAgentgatewayWrapper:
  hostname: gateway.routing.test
  llamacpp:
    enabled: true
    host: local.routing.test
    port: 11434
externalGateway:
  enabled: false
guardrails:
  llmPolicyEngine:
    enabled: true
    models:
      - name: remote/default
        provider: OpenAI
        model: default-model
        baseURL: https://default.routing.test
        piiEnabled: true
        contentTracingEnabled: true
        piiReroute: true
      - name: remote/code
        provider: OpenAI
        model: code-model
        baseURL: https://code.routing.test
        piiEnabled: true
        contentTracingEnabled: true
      - name: local/sensitive
        model: local-model
        local: true
        piiEnabled: false
        contentTracingEnabled: true
monitorPiiEngine:
  policy:
    routing:
      defaultTarget: local/sensitive
      targets:
        - name: local/sensitive
        - name: remote/code
          classPrefix: code/
""",
            encoding="utf-8",
        )
        gateway = subprocess.run(
            [
                "helm",
                "template",
                "agentgateway",
                str(AGENTGATEWAY_CHART),
                "--values",
                str(ROOT / "releases/shared/hostnames.yaml"),
                "--values",
                str(ROOT / "releases/shared/oidc-clients.yaml"),
                "--values",
                str(ROOT / "releases/shared/resources.yaml"),
                "--values",
                str(ROOT / "releases/shared/default-pii-settings.yaml"),
                "--values",
                str(fixture),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if gateway.returncode != 0:
            raise RuntimeError(gateway.stderr)
        cls.gateway_manifest = gateway.stdout

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove the synthetic AgentGateway routing values."""
        cls.temp_directory.cleanup()

    def test_extproc_release_and_dependency_readiness_contract(self) -> None:
        self.assertIn(
            "k8s-stack-agentgateway-extproc:0.1.0", self.extproc_manifest
        )
        self.assertIn("name: EXTPROC_ENGINE__READINESS_TIMEOUT", self.extproc_manifest)
        self.assertIn('value: "1"', self.extproc_manifest)
        self.assertRegex(
            self.extproc_manifest,
            r"(?s)readinessProbe:.*?path: /ready.*?timeoutSeconds: 2.*?"
            r"failureThreshold: 3.*?successThreshold: 1.*?livenessProbe:.*?path: /health",
        )

    def test_extproc_enforces_transport_limits_and_timeout_chain(self) -> None:
        expected_env = {
            "EXTPROC_MAX_REQUEST_BYTES": "5242880",
            "EXTPROC_MAX_RESPONSE_BYTES": "10485760",
            "EXTPROC_MAX_TRANSFORMED_REQUEST_BYTES": "10485760",
            "EXTPROC_GRPC_MAX_RECEIVE_MESSAGE_BYTES": "6356992",
            "EXTPROC_ENGINE__TIMEOUT": "615",
            "EXTPROC_ENGINE__MAX_RESPONSE_BYTES": "10485760",
        }
        for name, value in expected_env.items():
            with self.subTest(name=name):
                self.assertIn(
                    f'name: {name}\n              value: "{value}"',
                    self.extproc_manifest,
                )

        self.assertIn("requestTimeout: \"630s\"", self.gateway_manifest)

    def test_extproc_readiness_timeout_cannot_outlast_probe(self) -> None:
        oversized = subprocess.run(
            [
                "helm",
                "template",
                "agentgateway-extproc",
                str(AGENTGATEWAY_EXTPROC_CHART),
                "--set",
                "monitorAgentgatewayExtproc.engine.readinessTimeout=2",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        invalid_type = subprocess.run(
            [
                "helm",
                "template",
                "agentgateway-extproc",
                str(AGENTGATEWAY_EXTPROC_CHART),
                "--set",
                "monitorAgentgatewayExtproc.engine.readinessTimeout=true",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(oversized.returncode, 0)
        self.assertIn("readinessTimeout must be greater than zero", oversized.stderr)
        self.assertNotEqual(invalid_type.returncode, 0)
        self.assertIn("readinessTimeout must be a number", invalid_type.stderr)

    def test_offline_single_replica_recreate_and_read_only_runtime(self) -> None:
        self.assertIn("strategy:\n    type: Recreate", self.manifest)
        self.assertIn("replicas: 1", self.manifest)
        self.assertIn("HF_HUB_OFFLINE", self.manifest)
        self.assertIn("TRANSFORMERS_OFFLINE", self.manifest)
        self.assertIn("mountPath: /cache\n              readOnly: true", self.manifest)

    def test_cpu_and_cuda_images_have_explicit_scheduling_contracts(self) -> None:
        """Default to CPU and request an NVIDIA resource only for CUDA."""
        self.assertIn("k8s-stack-pii-engine:0.1.0-cpu", self.runtime_manifest)
        self.assertIn('name: PII_ENGINE_DEVICE\n              value: "cpu"', self.runtime_manifest)
        self.assertNotIn('"nvidia.com/gpu": 1', self.runtime_manifest)
        self.assertIn("k8s-stack-pii-engine:test-cu124", self.gpu_manifest)
        self.assertIn('name: PII_ENGINE_DEVICE\n              value: "cuda:0"', self.gpu_manifest)
        self.assertIn('"nvidia.com/gpu": 1', self.gpu_manifest)
        self.assertIn("accelerator: nvidia", self.gpu_manifest)
        self.assertIn("key: nvidia.com/gpu", self.gpu_manifest)

    def test_cuda_device_without_gpu_resource_fails_rendering(self) -> None:
        """Prevent a CUDA pod from starting without exclusive GPU scheduling."""
        result = subprocess.run(
            [
                "helm",
                "template",
                "pii-engine",
                str(CHART),
                "--set",
                "monitorPiiEngine.device=cuda:0",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("accelerator.enabled must be true", result.stderr)

    def test_runtime_and_model_sync_reject_wrong_image_variants(self) -> None:
        """Keep runtime dependencies aligned and model synchronization CPU-only."""
        runtime = subprocess.run(
            [
                "helm",
                "template",
                "pii-engine",
                str(CHART),
                "--set",
                "monitorPiiEngine.image=ghcr.io/neurwerk/k8s-stack-pii-engine:0.1.2-cu124",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        model_sync = subprocess.run(
            [
                "helm",
                "template",
                "pii-engine-model-sync",
                str(MODEL_SYNC_CHART),
                "--set",
                "monitorPiiEngine.modelSync.image=ghcr.io/neurwerk/k8s-stack-pii-engine:0.1.2-cu124",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(runtime.returncode, 0)
        self.assertIn("CPU variant", runtime.stderr)
        self.assertNotEqual(model_sync.returncode, 0)
        self.assertIn("CPU image variant", model_sync.stderr)

    def test_model_sync_isolated_from_runtime_policy(self) -> None:
        self.assertIn("monitor-pii-engine-model-sync", self.manifest)
        self.assertIn("app.kubernetes.io/component: model-sync", self.manifest)
        self.assertIn("monitor-pii-engine-model-sync-network-policy", self.manifest)
        self.assertIn("monitor-pii-engine-runtime-network-policy", self.manifest)
        self.assertNotIn("helm.sh/hook:", self.sync_manifest)
        self.assertIn("helm.sh/resource-policy: keep", self.sync_manifest)
        self.assertIn("kind: PersistentVolumeClaim", self.sync_manifest)
        self.assertNotIn("monitor-pii-engine-model-sync-network-policy", self.runtime_manifest)
        self.assertIn("k8s-stack-pii-engine:0.1.0-cpu", self.sync_manifest)

    def test_runtime_enforces_analysis_limits_and_timeouts(self) -> None:
        expected_env = {
            "PII_ENGINE_MAX_REQUEST_BYTES": "5242880",
            "PII_ENGINE_MAX_ADAPTER_RESPONSE_BYTES": "10485760",
            "PII_ENGINE_MAX_STUDIO_EVALUATION_RESPONSE_BYTES": "10485760",
            "PII_ENGINE_MAX_TEXT_CHARACTERS": "4000000",
            "PII_ENGINE_ANALYSIS_TIMEOUT": "600",
            "PII_ENGINE_STUDIO_ANALYSIS_TIMEOUT": "30",
        }
        for name, value in expected_env.items():
            with self.subTest(name=name):
                self.assertIn(
                    f'name: {name}\n              value: "{value}"',
                    self.runtime_manifest,
                )

        self.assertRegex(
            self.runtime_manifest,
            r"(?s)policy.yaml:.*?pii:.*?timeout: 600",
        )
        shared_policy = (ROOT / "releases/shared/default-pii-settings.yaml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(shared_policy, r"(?s)pii:.*?timeout: 600")

    def test_studio_evaluation_response_limit_matches_service_bounds(self) -> None:
        """Reject values outside the service's hard 1 KiB through 10 MiB contract."""
        for value in ("1023", "10485761", "1.5"):
            result = subprocess.run(
                [
                    "helm",
                    "template",
                    "pii-engine",
                    str(CHART),
                    "--set-string",
                    f"monitorPiiEngine.capacity.maxStudioEvaluationResponseBytes={value}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            with self.subTest(value=value):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("maxStudioEvaluationResponseBytes must be", result.stderr)

    def test_runtime_logging_defaults_to_info(self) -> None:
        """Keep full exception tracebacks behind an explicit temporary override."""
        self.assertIn(
            'name: PII_ENGINE_LOG_LEVEL\n              value: "INFO"', self.runtime_manifest
        )

    def test_runtime_logging_rejects_unknown_levels(self) -> None:
        """Reject values the PII Engine settings contract cannot load."""
        result = subprocess.run(
            [
                "helm",
                "template",
                "pii-engine",
                str(CHART),
                "--set",
                "monitorPiiEngine.logging.level=TRACE",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("logging.level must be DEBUG, INFO, WARNING, or ERROR", result.stderr)

    def test_model_sync_periodically_activates_the_pinned_bundle(self) -> None:
        """The CronJob tolerates absence but never changes the pinned bundle identity."""
        self.assertIn("kind: CronJob", self.sync_manifest)
        self.assertIn("name: monitor-pii-engine-model-sync", self.sync_manifest)
        self.assertIn('schedule: "*/15 * * * *"', self.sync_manifest)
        self.assertIn("concurrencyPolicy: Forbid", self.sync_manifest)
        self.assertIn("type: RuntimeDefault", self.sync_manifest)
        self.assertIn("backoffLimit: 2", self.sync_manifest)
        self.assertIn("--missing-manifest-ok", self.sync_manifest)
        self.assertIn("- port: 8080", self.sync_manifest)

    def test_runtime_preserves_presence_ordering_and_verified_media_pins(self) -> None:
        """The baseline runtime and optional synchronizer retain identical media pins."""
        runtime_release = RUNTIME_RELEASE.read_text(encoding="utf-8")
        sync_release = MODEL_SYNC_RELEASE.read_text(encoding="utf-8")
        digest = "a24eb93bc245e220f76ac010a57cf540a6399b2046b2782a07035ae660cc3926"
        for release in (runtime_release, sync_release):
            self.assertIn('version: "0.1.2"', release)
            self.assertIn(f"manifestSha256: {digest}", release)
        self.assertIn("- name: pii-engine-model-sync", runtime_release)
        self.assertIn("--reference-path", self.sync_manifest)
        self.assertIn("/cache/desired-bundle.json", self.sync_manifest)
        self.assertIn("PII_ENGINE_MODEL_BUNDLE_REFERENCE", self.runtime_manifest)

    def test_model_sync_waits_through_its_first_cron_schedule(self) -> None:
        release = MODEL_SYNC_RELEASE.read_text(encoding="utf-8")
        self.assertIn("timeout: 20m", release)
        self.assertEqual(release.count("name: RetryOnFailure"), 2)
        self.assertIn("- name: rook-ceph", release)

    def test_empty_cache_uses_the_bundled_baseline_until_activation(self) -> None:
        """Only model sync writes the cache; runtime can start before media publication."""
        self.assertIn("kind: PersistentVolumeClaim", self.sync_manifest)
        self.assertIn("name: monitor-pii-engine-model-cache-pvc", self.sync_manifest)
        self.assertIn("mountPath: /cache\n              readOnly: true", self.runtime_manifest)
        self.assertIn("missing-manifest-ok", self.sync_manifest)
        self.assertIn("strategy: multilingual", self.runtime_manifest)
        self.assertIn("en: english-pii", self.runtime_manifest)

    def test_model_sync_has_no_kubernetes_api_activation_privilege(self) -> None:
        """The Job validates media only; matching Helm values perform the rollout."""
        self.assertIn("automountServiceAccountToken: false", self.sync_manifest)
        self.assertNotIn("client.patch_namespaced_deployment", self.sync_manifest)
        self.assertNotIn("service-account-token", self.sync_manifest)
        self.assertNotIn("discard-stale-model-reference", self.runtime_manifest)

    def test_runtime_uses_local_valkey_without_object_store_credentials(self) -> None:
        self.assertIn("name: monitor-pii-engine-valkey\n", self.runtime_manifest)
        self.assertIn("monitor-pii-engine-valkey-primary:6379", self.runtime_manifest)
        self.assertIn("monitor-pii-engine-valkey-client: \"true\"", self.runtime_manifest)
        self.assertIn("docker.io/valkey/valkey:9.1.1-alpine", self.runtime_manifest)
        self.assertNotIn("bitnami/valkey", self.runtime_manifest)
        self.assertNotIn("AWS_ACCESS_KEY_ID", self.runtime_manifest)

    def test_mtls_and_management_ports_are_declared(self) -> None:
        self.assertIn("kind: Certificate", self.manifest)
        self.assertIn("- server auth", self.manifest)
        self.assertIn("containerPort: 8443", self.manifest)
        self.assertIn("containerPort: 8001", self.manifest)
        self.assertIn("path: /metrics", self.manifest)

    def test_extproc_targets_only_the_pii_runtime_port(self) -> None:
        self.assertIn(
            """kubernetes.io/metadata.name: monitor-pii-engine
          podSelector:
            matchLabels:
              app.kubernetes.io/name: pii-engine
              app.kubernetes.io/component: runtime
      ports:
        # The PII Engine Service maps port 443 to Pod port 8443.
        - port: 8443""",
            self.extproc_manifest,
        )
        self.assertNotIn("- port: 8001", self.extproc_manifest)

    def test_model_sync_has_a_cross_namespace_read_only_rgw_identity(self) -> None:
        self.assertIn("allowUsersInNamespaces:\n    - monitor-pii-engine", self.rook_manifest)
        self.assertRegex(
            self.rook_manifest,
            r"(?s)name: pii-model-sync\n  namespace: monitor-pii-engine.*?"
            r"clusterNamespace: infra-rook-ceph.*?opMask:\n    - read",
        )
        self.assertIn("bucketOwner: pii-publisher", self.rook_manifest)
        self.assertIn(
            "name: rook-ceph-object-user-infra-rook-ceph-object-store-pii-model-sync",
            self.sync_manifest,
        )

    def test_route_class_selects_only_configured_safe_direction_targets(self) -> None:
        """Consume trusted classes while retaining the remote-allowed gate."""
        self.assertIn(
            r'request.headers[\"x-route-class\"] == \"local/sensitive\"',
            self.gateway_manifest,
        )
        self.assertIn(
            r'request.headers[\"x-remote-allowed\"] == \"true\" && '
            r'(request.headers[\"x-route-class\"].startsWith(\"code/\"))',
            self.gateway_manifest,
        )
        self.assertNotIn(
            r'when: "request.headers[\"x-route-class\"].startsWith(\"code/\")"',
            self.gateway_manifest,
        )
        self.assertIn("name: local-sensitive", self.gateway_manifest)
        self.assertIn("name: remote-code", self.gateway_manifest)

    def test_agentgateway_loads_client_values(self) -> None:
        """Keep policy routing targets aligned with each client's engine policy."""
        release = AGENTGATEWAY_RELEASE.read_text(encoding="utf-8")
        self.assertIn("name: client-values", release)


if __name__ == "__main__":
    unittest.main()
