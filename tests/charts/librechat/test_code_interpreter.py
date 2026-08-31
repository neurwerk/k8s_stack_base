"""Rendered security and topology contracts for LibreChat Code Interpreter."""

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


NAMESPACE = "librechat-code-interpreter"
SOURCE_SHA = "fea707467600f3802d65596a6875c7822f25cfd8"


def render_code_interpreter(
    chart: str,
    *,
    values: tuple[Path, ...] = (),
    platform_values: bool = True,
    extra_args: tuple[str, ...] = (),
    check: bool = True,
):
    """Render one Code Interpreter leaf chart with its deployed identity."""
    name = f"librechat-code-interpreter-{Path(chart).name}"
    return render_chart(
        f"code-interpreter/{chart}",
        release_name=name,
        namespace=NAMESPACE,
        values=values,
        platform_values=platform_values,
        extra_args=extra_args,
        check=check,
    )


class CodeInterpreterTopologyTests(unittest.TestCase):
    """Keep every service private, pinned, and separately deployable."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifests = {
            chart: render_code_interpreter(chart).stdout
            for chart in (
                "shared",
                "valkey",
                "package-init",
                "file-server",
                "tool-call-server",
                "egress-gateway",
                "worker",
                "api",
            )
        }

    def test_service_charts_render_expected_workload_service_and_image(self) -> None:
        expected = {
            "valkey": (
                "StatefulSet",
                "librechat-code-interpreter-valkey",
                "librechat-code-interpreter-valkey",
                "docker.io/valkey/valkey:9.1.1-alpine",
            ),
            "file-server": (
                "Deployment",
                "librechat-code-interpreter-file-server",
                "librechat-code-interpreter-file-server",
                f"ghcr.io/neurwerk/librechat-code-interpreter-file-server:{SOURCE_SHA}",
            ),
            "tool-call-server": (
                "Deployment",
                "librechat-code-interpreter-tool-call-server",
                "librechat-code-interpreter-tool-call-server",
                f"ghcr.io/neurwerk/librechat-code-interpreter-tool-call-server:{SOURCE_SHA}",
            ),
            "egress-gateway": (
                "Deployment",
                "librechat-code-interpreter-egress-gateway",
                "librechat-code-interpreter-egress-gateway",
                f"ghcr.io/neurwerk/librechat-code-interpreter-egress-gateway:{SOURCE_SHA}",
            ),
            "api": (
                "Deployment",
                "librechat-code-interpreter-api",
                "librechat-code-interpreter-api",
                f"ghcr.io/neurwerk/librechat-code-interpreter-api:{SOURCE_SHA}",
            ),
        }
        for chart, (kind, workload_name, service_name, image) in expected.items():
            with self.subTest(chart=chart):
                workload = resource(
                    self.manifests[chart], kind, workload_name
                )
                service = resource(
                    self.manifests[chart], "Service", service_name
                )
                self.assertIn(f'image: "{image}"', workload)
                self.assertIn("type: ClusterIP", service)
                self.assertNotRegex(service, r"(?m)^\s+type: (?:LoadBalancer|NodePort)$")

    def test_shared_chart_owns_foundation_storage_and_network_resources(self) -> None:
        manifest = self.manifests["shared"]
        self.assertEqual(len(resources_of_kind(manifest, "Secret")), 6)
        self.assertEqual(len(resources_of_kind(manifest, "ObjectBucketClaim")), 1)
        self.assertEqual(len(resources_of_kind(manifest, "PersistentVolumeClaim")), 1)
        self.assertEqual(len(resources_of_kind(manifest, "ServiceAccount")), 1)
        self.assertEqual(len(resources_of_kind(manifest, "NetworkPolicy")), 2)
        self.assertEqual(len(resources_of_kind(manifest, "Job")), 0)
        self.assertEqual(len(resources_of_kind(manifest, "Deployment")), 0)
        self.assertEqual(len(resources_of_kind(manifest, "Service")), 0)
        default_deny = resource(
            manifest,
            "NetworkPolicy",
            "librechat-code-interpreter-default-deny",
        )
        self.assertIn("podSelector: {}", default_deny)
        self.assertIn("- Ingress", default_deny)
        self.assertIn("- Egress", default_deny)

    def test_shared_chart_owns_package_claim_account_and_policy(self) -> None:
        manifest = self.manifests["shared"]
        claim = resource(
            manifest,
            "PersistentVolumeClaim",
            "librechat-code-interpreter-packages",
        )
        resource(
            manifest,
            "ServiceAccount",
            "librechat-code-interpreter-package-init",
        )
        resource(
            manifest,
            "NetworkPolicy",
            "librechat-code-interpreter-package-init",
        )
        self.assertIn("helm.sh/resource-policy: keep", claim)
        self.assertIn("- ReadWriteOnce", claim)

    def test_package_init_chart_renders_only_a_retry_safe_hook_job(self) -> None:
        manifest = self.manifests["package-init"]
        jobs = resources_of_kind(manifest, "Job")
        self.assertEqual(len(jobs), 1)
        self.assertRegex(
            jobs[0],
            r"(?m)^  name: librechat-code-interpreter-package-init-[a-f0-9]{10}$",
        )
        self.assertIn(
            f'image: "ghcr.io/neurwerk/librechat-code-interpreter-package-init:{SOURCE_SHA}"',
            jobs[0],
        )
        self.assertIn("activeDeadlineSeconds: 5100", jobs[0])
        self.assertIn("claimName: librechat-code-interpreter-packages", jobs[0])
        self.assertIn('"helm.sh/hook": pre-install,pre-upgrade', jobs[0])
        self.assertIn(
            '"helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded',
            jobs[0],
        )
        self.assertNotIn("hook-failed", jobs[0])
        self.assertNotIn("ttlSecondsAfterFinished", jobs[0])
        self.assertEqual(resources_of_kind(manifest, "PersistentVolumeClaim"), [])
        self.assertEqual(resources_of_kind(manifest, "ServiceAccount"), [])
        self.assertEqual(resources_of_kind(manifest, "NetworkPolicy"), [])

    def test_worker_chart_owns_only_runtime_workers_and_profiles(self) -> None:
        manifest = self.manifests["worker"]
        service_worker = resource(
            manifest,
            "Deployment",
            "librechat-code-interpreter-worker-service-worker",
        )
        sandbox = resource(
            manifest,
            "Deployment",
            "librechat-code-interpreter-worker-sandbox-runner",
        )
        resource(
            manifest,
            "Service",
            "librechat-code-interpreter-worker-sandbox-runner",
        )
        self.assertIn(
            f'image: "ghcr.io/neurwerk/librechat-code-interpreter-worker:{SOURCE_SHA}"',
            service_worker,
        )
        self.assertEqual(
            sandbox.count(
                f'image: "ghcr.io/neurwerk/'
                f'librechat-code-interpreter-sandbox-runner:{SOURCE_SHA}"'
            ),
            2,
        )
        self.assertIn("claimName: librechat-code-interpreter-packages", sandbox)
        self.assertEqual(resources_of_kind(manifest, "Job"), [])
        self.assertEqual(resources_of_kind(manifest, "PersistentVolumeClaim"), [])
        self.assertNotIn("app.kubernetes.io/component: package-init", manifest)
        resource(
            manifest,
            "ConfigMap",
            "librechat-code-interpreter-nsjail-seccomp",
        )
        resource(
            manifest,
            "ConfigMap",
            "librechat-code-interpreter-nsjail-apparmor",
        )

    def test_package_job_name_is_retry_stable_and_covers_template_values(self) -> None:
        baseline_job = resources_of_kind(self.manifests["package-init"], "Job")[0]
        retry_job = resources_of_kind(
            render_code_interpreter("package-init").stdout,
            "Job",
        )[0]
        changed_manifest = render_code_interpreter(
            "package-init",
            extra_args=(
                "--set-string",
                "frontendLibrechatCodeInterpreter.packages.initJob.resources.requests.cpu=750m",
            ),
        ).stdout
        changed_job = resources_of_kind(changed_manifest, "Job")[0]
        name_pattern = r"(?m)^  name: (librechat-code-interpreter-package-init-[a-f0-9]{10})$"
        baseline_name = re.search(name_pattern, baseline_job)
        retry_name = re.search(name_pattern, retry_job)
        changed_name = re.search(name_pattern, changed_job)
        self.assertIsNotNone(baseline_name, baseline_job)
        self.assertIsNotNone(retry_name, retry_job)
        self.assertIsNotNone(changed_name, changed_job)
        self.assertEqual(baseline_name.group(1), retry_name.group(1))
        self.assertNotEqual(baseline_name.group(1), changed_name.group(1))

    def test_no_code_interpreter_chart_exposes_a_public_route_or_service(self) -> None:
        combined = "\n---\n".join(self.manifests.values())
        for kind in ("Gateway", "HTTPRoute", "Ingress"):
            self.assertEqual(resources_of_kind(combined, kind), [])
        self.assertNotRegex(combined, r"(?m)^\s+type: (?:LoadBalancer|NodePort)$")


class CodeInterpreterProductionValidationTests(unittest.TestCase):
    """Require production scheduling, persistence, and sandbox hardening."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_directory = tempfile.TemporaryDirectory(prefix="code-interpreter-")
        cls.temp_path = Path(cls.temp_directory.name)
        cls.valid_worker_values = cls.write_values(
            "valid-worker.yaml",
            """frontendLibrechatCodeInterpreter:
  validation:
    production: true
  scheduling:
    nodeSelector:
      code-interpreter.neurwerk.com/dedicated: "true"
    tolerations:
      - key: code-interpreter.neurwerk.com/dedicated
        operator: Equal
        value: "true"
        effect: NoSchedule
  packages:
    persistence:
      storageClassName: test-rbd
""",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_directory.cleanup()

    @classmethod
    def write_values(cls, filename: str, content: str) -> Path:
        path = cls.temp_path / filename
        path.write_text(content, encoding="ascii")
        return path

    def test_valid_production_worker_contract_renders(self) -> None:
        result = render_code_interpreter(
            "worker",
            values=(self.valid_worker_values,),
            platform_values=False,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_valid_production_package_init_contract_renders(self) -> None:
        result = render_code_interpreter(
            "package-init",
            values=(self.valid_worker_values,),
            platform_values=False,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_requires_dedicated_node_selector_and_toleration(self) -> None:
        missing_node_selector = self.write_values(
            "missing-node-selector.yaml",
            """frontendLibrechatCodeInterpreter:
  validation:
    production: true
  scheduling:
    tolerations:
      - key: dedicated
        operator: Exists
        effect: NoSchedule
  packages:
    persistence:
      storageClassName: test-rbd
""",
        )
        missing_toleration = self.write_values(
            "missing-toleration.yaml",
            """frontendLibrechatCodeInterpreter:
  validation:
    production: true
  scheduling:
    nodeSelector:
      dedicated: "true"
  packages:
    persistence:
      storageClassName: test-rbd
""",
        )
        for chart in ("package-init", "worker"):
            for fixture, error in (
                (missing_node_selector, "scheduling.nodeSelector must be non-empty"),
                (missing_toleration, "scheduling.tolerations must be non-empty"),
            ):
                with self.subTest(chart=chart, error=error):
                    result = render_code_interpreter(
                        chart,
                        values=(fixture,),
                        platform_values=False,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(error, result.stderr)

    def test_production_rejects_weakened_direct_nsjail_settings(self) -> None:
        invalid_settings = (
            (
                "frontendLibrechatCodeInterpreter.hardenedSandboxMode=false",
                "hardenedSandboxMode must remain true",
            ),
            (
                "frontendLibrechatCodeInterpreter.sandbox.security.privileged=true",
                "sandbox.security.privileged must remain false",
            ),
            (
                "frontendLibrechatCodeInterpreter.sandbox.security.allowPrivilegeEscalation=true",
                "sandbox.security.allowPrivilegeEscalation must remain false",
            ),
            (
                "frontendLibrechatCodeInterpreter.sandbox.security.seccomp.enabled=false",
                "sandbox.security.seccomp.enabled must remain true",
            ),
            (
                "frontendLibrechatCodeInterpreter.sandbox.security.appArmor.enabled=false",
                "sandbox.security.appArmor.enabled must remain true",
            ),
        )
        for setting, error in invalid_settings:
            with self.subTest(setting=setting):
                result = render_code_interpreter(
                    "worker",
                    values=(self.valid_worker_values,),
                    platform_values=False,
                    extra_args=("--set", setting),
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)

    def test_production_rejects_disabled_package_storage(self) -> None:
        result = render_code_interpreter(
            "shared",
            extra_args=(
                "--set",
                "frontendLibrechatCodeInterpreter.packages.persistence.enabled=false",
            ),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("packages.persistence.enabled must remain true", result.stderr)

    def test_production_rejects_disabled_package_initialization(self) -> None:
        result = render_code_interpreter(
            "package-init",
            values=(self.valid_worker_values,),
            platform_values=False,
            extra_args=(
                "--set",
                "frontendLibrechatCodeInterpreter.packages.initJob.enabled=false",
            ),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("packages.initJob.enabled must remain true", result.stderr)

    def test_package_storage_requires_supported_access_mode_and_retention(self) -> None:
        invalid_settings = (
            (
                "frontendLibrechatCodeInterpreter.packages.persistence.accessMode=ReadOnlyMany",
                "packages.persistence.accessMode must be ReadWriteOnce or ReadWriteMany",
            ),
            (
                "frontendLibrechatCodeInterpreter.packages.persistence.lifecycle=Delete",
                "packages.persistence.lifecycle must be Retain",
            ),
        )
        for setting, error in invalid_settings:
            with self.subTest(setting=setting):
                result = render_code_interpreter(
                    "shared",
                    extra_args=("--set-string", setting),
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)

    def test_package_storage_class_is_required_in_production(self) -> None:
        result = render_code_interpreter(
            "shared",
            extra_args=(
                "--set-string",
                "frontendLibrechatCodeInterpreter.packages.persistence.storageClassName=",
            ),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "packages.persistence.storageClassName is required from client-owned values",
            result.stderr,
        )

    def test_rwo_package_claim_rejects_multiple_sandbox_replicas(self) -> None:
        result = render_code_interpreter(
            "worker",
            values=(self.valid_worker_values,),
            platform_values=False,
            extra_args=(
                "--set",
                "frontendLibrechatCodeInterpreter.sandbox.replicas=2",
            ),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "sandbox.replicas greater than one requires a client-selected ReadWriteMany package claim",
            result.stderr,
        )

    def test_client_selected_rwx_claim_allows_multiple_sandbox_replicas(self) -> None:
        access_mode = (
            "frontendLibrechatCodeInterpreter.packages.persistence.accessMode="
            "ReadWriteMany"
        )
        package_result = render_code_interpreter(
            "shared",
            extra_args=("--set-string", access_mode),
            check=False,
        )
        self.assertEqual(package_result.returncode, 0, package_result.stderr)
        package_claim = resource(
            package_result.stdout,
            "PersistentVolumeClaim",
            "librechat-code-interpreter-packages",
        )
        self.assertIn("- ReadWriteMany", package_claim)

        worker_result = render_code_interpreter(
            "worker",
            values=(self.valid_worker_values,),
            platform_values=False,
            extra_args=(
                "--set-string",
                access_mode,
                "--set",
                "frontendLibrechatCodeInterpreter.sandbox.replicas=2",
            ),
            check=False,
        )
        self.assertEqual(worker_result.returncode, 0, worker_result.stderr)

    def test_production_rejects_any_capability_set_change(self) -> None:
        reduced_capabilities = self.write_values(
            "reduced-capabilities.yaml",
            """frontendLibrechatCodeInterpreter:
  sandbox:
    security:
      capabilities:
        - SYS_ADMIN
""",
        )
        result = render_code_interpreter(
            "worker",
            values=(self.valid_worker_values, reduced_capabilities),
            platform_values=False,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "sandbox.security.capabilities must remain the documented direct-NsJail capability set",
            result.stderr,
        )

    def test_production_valkey_requires_retained_client_selected_storage(self) -> None:
        missing_storage = render_code_interpreter(
            "valkey",
            platform_values=False,
            extra_args=(
                "--set",
                "frontendLibrechatCodeInterpreter.validation.production=true",
            ),
            check=False,
        )
        self.assertNotEqual(missing_storage.returncode, 0)
        self.assertIn(
            "valkey.persistence.storageClassName is required from client-owned values",
            missing_storage.stderr,
        )
        non_retained = render_code_interpreter(
            "valkey",
            platform_values=False,
            extra_args=(
                "--set-string",
                "frontendLibrechatCodeInterpreter.valkey.persistence.lifecycle=Delete",
            ),
            check=False,
        )
        self.assertNotEqual(non_retained.returncode, 0)
        self.assertIn("valkey.persistence.lifecycle must be Retain", non_retained.stderr)

    def test_production_shared_chart_rejects_missing_runtime_secrets(self) -> None:
        result = render_code_interpreter(
            "shared",
            platform_values=False,
            extra_args=(
                "--set",
                "frontendLibrechatCodeInterpreter.validation.production=true",
                "--set-string",
                "frontendLibrechatCodeInterpreter.packages.persistence.storageClassName=test-rbd",
            ),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "frontendLibrechatCodeInterpreterSecrets.internalServiceToken is required",
            result.stderr,
        )


class CodeInterpreterSandboxSecurityTests(unittest.TestCase):
    """Pin direct NsJail privileges, network paths, and credential exposure."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifests = {
            chart: render_code_interpreter(chart).stdout
            for chart in (
                "shared",
                "valkey",
                "package-init",
                "file-server",
                "tool-call-server",
                "egress-gateway",
                "worker",
                "api",
            )
        }

    def test_nsjail_has_no_kvm_and_uses_only_the_exact_capability_set(self) -> None:
        worker = self.manifests["worker"]
        sandbox = resource(
            worker,
            "Deployment",
            "librechat-code-interpreter-worker-sandbox-runner",
        )
        self.assertIn('name: KVM_ENABLED\n              value: "false"', sandbox)
        for forbidden in (
            "/dev/kvm",
            "hostPath:",
            "devices.kubevirt.io/kvm",
            "supplementalGroups:",
        ):
            self.assertNotIn(forbidden, worker)
        self.assertIn("privileged: false", sandbox)
        self.assertIn("allowPrivilegeEscalation: false", sandbox)
        capability_block = re.search(
            r"(?ms)^              add:\n(?P<body>(?:                - [A-Z_]+\n?)+)",
            sandbox,
        )
        self.assertIsNotNone(capability_block, sandbox)
        self.assertEqual(
            re.findall(r"- ([A-Z_]+)", capability_block.group("body")),
            [
                "SYS_ADMIN",
                "SYS_CHROOT",
                "SYS_PTRACE",
                "SETUID",
                "SETGID",
                "NET_ADMIN",
                "DAC_OVERRIDE",
                "DAC_READ_SEARCH",
                "CHOWN",
                "FOWNER",
                "FSETID",
                "KILL",
                "SETFCAP",
                "MKNOD",
            ],
        )
        self.assertIn("type: Localhost", sandbox)
        self.assertIn(
            'localhostProfile: "profiles/librechat-code-interpreter-nsjail.json"',
            sandbox,
        )
        self.assertIn(
            'localhostProfile: "librechat-code-interpreter-nsjail"',
            sandbox,
        )

    def test_valkey_probes_require_authenticated_success(self) -> None:
        valkey = resource(
            self.manifests["valkey"],
            "StatefulSet",
            "librechat-code-interpreter-valkey",
        )
        self.assertIn("name: REDISCLI_AUTH", valkey)
        self.assertNotIn("name: VALKEYCLI_AUTH", valkey)
        self.assertEqual(
            valkey.count('["valkey-cli", "-e", "-h", "127.0.0.1", "ping"]'),
            2,
        )

    def test_sandbox_network_is_confined_to_the_egress_gateway(self) -> None:
        worker = self.manifests["worker"]
        sandbox_policy = resource(
            worker,
            "NetworkPolicy",
            "librechat-code-interpreter-worker-sandbox-runner",
        )
        package_policy = resource(
            self.manifests["shared"],
            "NetworkPolicy",
            "librechat-code-interpreter-package-init",
        )
        combined = "\n---\n".join(self.manifests.values())
        self.assertIn(
            "app.kubernetes.io/name: librechat-code-interpreter-egress-gateway",
            sandbox_policy,
        )
        self.assertIn("port: 3190", sandbox_policy)
        self.assertEqual(sandbox_policy.count("- to:"), 1)
        self.assertNotIn("kube-dns", sandbox_policy)
        self.assertNotIn("ipBlock:", sandbox_policy)
        self.assertNotIn("0.0.0.0/0", sandbox_policy)
        self.assertIn("ipBlock:", package_policy)
        self.assertIn("cidr: 0.0.0.0/0", package_policy)
        self.assertIn("port: 443", package_policy)
        self.assertIn("k8s-app: kube-dns", package_policy)
        self.assertEqual(package_policy.count("port: 53"), 2)
        self.assertIn("protocol: UDP", package_policy)
        self.assertIn("protocol: TCP", package_policy)
        self.assertIn("ingress: []", package_policy)
        for denied_range in (
            "10.0.0.0/8",
            "100.64.0.0/10",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "172.16.0.0/12",
            "192.168.0.0/16",
        ):
            self.assertIn(denied_range, package_policy)
        self.assertEqual(combined.count("ipBlock:"), 1)
        self.assertNotIn("namespaceSelector: {}", combined)

    def test_api_is_reachable_only_from_the_labeled_librechat_app(self) -> None:
        policy = resource(
            self.manifests["api"],
            "NetworkPolicy",
            "librechat-code-interpreter-api",
        )
        self.assertIn("kubernetes.io/metadata.name: frontend-librechat", policy)
        self.assertIn("app.kubernetes.io/name: frontend-librechat", policy)
        self.assertIn("port: 3112", policy)
        self.assertNotIn("ipBlock:", policy)
        self.assertNotIn("podSelector: {}", policy)

    def test_obc_and_package_claims_are_retained(self) -> None:
        claim = resource(
            self.manifests["shared"],
            "ObjectBucketClaim",
            "librechat-code-interpreter-files",
        )
        file_server = resource(
            self.manifests["file-server"],
            "Deployment",
            "librechat-code-interpreter-file-server",
        )
        self.assertIn("helm.sh/resource-policy: keep", claim)
        self.assertIn("bucketName: librechat-code-interpreter-files", claim)
        self.assertIn(
            "storageClassName: infra-rook-ceph-object-bucket",
            claim,
        )
        self.assertEqual(
            file_server.count("name: librechat-code-interpreter-files"),
            2,
        )
        self.assertIn(
            "secret.reloader.stakater.com/reload: "
            '"librechat-code-interpreter-internal-auth,'
            "librechat-code-interpreter-valkey-auth,"
            'librechat-code-interpreter-files"',
            file_server,
        )
        self.assertIn("key: AWS_ACCESS_KEY_ID", file_server)
        self.assertIn("key: AWS_SECRET_ACCESS_KEY", file_server)
        file_server_policy = resource(
            self.manifests["file-server"],
            "NetworkPolicy",
            "librechat-code-interpreter-file-server",
        )
        self.assertIn('name: MINIO_PORT\n              value: "80"', file_server)
        self.assertIn("port: 8080", file_server_policy)

        package_claim = resource(
            self.manifests["shared"],
            "PersistentVolumeClaim",
            "librechat-code-interpreter-packages",
        )
        self.assertIn("helm.sh/resource-policy: keep", package_claim)
        self.assertIn("storageClassName: \"infra-rook-ceph-rbd\"", package_claim)

        result = render_code_interpreter(
            "shared",
            platform_values=False,
            extra_args=(
                "--set-string",
                "frontendLibrechatCodeInterpreter.objectStorage.lifecycle=Delete",
            ),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("objectStorage.lifecycle must be Retain", result.stderr)

    def test_each_workload_receives_only_its_required_secret_classes(self) -> None:
        expected = {
            ("valkey", "StatefulSet", "librechat-code-interpreter-valkey"): {
                "librechat-code-interpreter-valkey-auth"
            },
            (
                "file-server",
                "Deployment",
                "librechat-code-interpreter-file-server",
            ): {
                "librechat-code-interpreter-files",
                "librechat-code-interpreter-valkey-auth",
                "librechat-code-interpreter-internal-auth",
            },
            (
                "tool-call-server",
                "Deployment",
                "librechat-code-interpreter-tool-call-server",
            ): {
                "librechat-code-interpreter-valkey-auth",
                "librechat-code-interpreter-internal-auth",
            },
            (
                "egress-gateway",
                "Deployment",
                "librechat-code-interpreter-egress-gateway",
            ): {
                "librechat-code-interpreter-egress-grant",
                "librechat-code-interpreter-internal-auth",
                "librechat-code-interpreter-valkey-auth",
            },
            (
                "worker",
                "Deployment",
                "librechat-code-interpreter-worker-service-worker",
            ): {
                "librechat-code-interpreter-internal-auth",
                "librechat-code-interpreter-valkey-auth",
                "librechat-code-interpreter-execution-signer",
            },
            (
                "worker",
                "Deployment",
                "librechat-code-interpreter-worker-sandbox-runner",
            ): {"librechat-code-interpreter-execution-verifier"},
            ("api", "Deployment", "librechat-code-interpreter-api"): {
                "librechat-code-interpreter-jwt-verifier",
                "librechat-code-interpreter-valkey-auth",
                "librechat-code-interpreter-internal-auth",
            },
        }
        for (chart, kind, name), names in expected.items():
            with self.subTest(workload=name):
                workload = resource(self.manifests[chart], kind, name)
                self.assertEqual(secret_ref_names(workload), names)

    def test_split_secret_payloads_do_not_leak_to_non_secret_resources(self) -> None:
        markers = {
            "internalServiceToken": "marker-internal-token",
            "valkeyPassword": "marker-valkey-password",
            "egressGrantSecret": "marker-egress-grant",
            "executionManifestPrivateKey": "marker-execution-private",
            "executionManifestPublicKey": "marker-execution-public",
            "jwtPublicKey": "marker-jwt-public",
        }
        with tempfile.TemporaryDirectory(prefix="code-interpreter-secrets-") as directory:
            fixture = Path(directory) / "secrets.yaml"
            fixture.write_text(
                "frontendLibrechatCodeInterpreterSecrets:\n"
                + "".join(f"  {key}: {value}\n" for key, value in markers.items()),
                encoding="ascii",
            )
            manifest = render_code_interpreter(
                "shared",
                values=(fixture,),
                platform_values=False,
            ).stdout

        expected_secrets = {
            "librechat-code-interpreter-internal-auth": "marker-internal-token",
            "librechat-code-interpreter-valkey-auth": "marker-valkey-password",
            "librechat-code-interpreter-egress-grant": "marker-egress-grant",
            "librechat-code-interpreter-execution-signer": "marker-execution-private",
            "librechat-code-interpreter-execution-verifier": "marker-execution-public",
            "librechat-code-interpreter-jwt-verifier": "marker-jwt-public",
        }
        all_markers = set(markers.values())
        for name, expected_marker in expected_secrets.items():
            secret = resource(manifest, "Secret", name)
            with self.subTest(secret=name):
                self.assertIn(expected_marker, secret)
                self.assertEqual(
                    {marker for marker in all_markers if marker in secret},
                    {expected_marker},
                )
        for marker in all_markers:
            self.assertNotIn(marker, non_secret_documents(manifest))


class CodeInterpreterReleaseGraphTests(unittest.TestCase):
    """Keep the release package ordered along the private service graph."""

    def test_kustomization_lists_all_split_code_interpreter_releases(self) -> None:
        content = (
            ROOT / "releases/librechat/code-interpreter/kustomization.yaml"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            re.findall(r"(?m)^  - ([a-z0-9-]+\.yaml)$", content),
            [
                "shared.yaml",
                "valkey.yaml",
                "package-init.yaml",
                "file-server.yaml",
                "tool-call-server.yaml",
                "egress-gateway.yaml",
                "worker.yaml",
                "api.yaml",
            ],
        )

    def test_release_chart_paths_match_leaf_components(self) -> None:
        for component in (
            "shared",
            "valkey",
            "package-init",
            "file-server",
            "tool-call-server",
            "egress-gateway",
            "worker",
            "api",
        ):
            with self.subTest(component=component):
                content = (
                    ROOT
                    / "releases/librechat/code-interpreter"
                    / f"{component}.yaml"
                ).read_text(encoding="utf-8")
                self.assertIn(
                    f"chart: ./charts/librechat/code-interpreter/{component}",
                    content,
                )
                if component == "shared":
                    self.assertIn(
                        "name: frontend-librechat-code-interpreter-runtime-secret",
                        content,
                    )
                else:
                    self.assertNotIn(
                        "name: frontend-librechat-code-interpreter-runtime-secret",
                        content,
                    )

    def test_package_release_retries_hook_and_worker_depends_on_it(self) -> None:
        package_release = (
            ROOT / "releases/librechat/code-interpreter/package-init.yaml"
        ).read_text(encoding="utf-8")
        shared_release = (
            ROOT / "releases/librechat/code-interpreter/shared.yaml"
        ).read_text(encoding="utf-8")
        worker_release = (
            ROOT / "releases/librechat/code-interpreter/worker.yaml"
        ).read_text(encoding="utf-8")

        self.assertEqual(package_release.count("name: RetryOnFailure"), 2)
        self.assertNotIn("disableHooks: true", package_release)
        self.assertNotIn("waitStrategy:", package_release)
        self.assertEqual(shared_release.count("disableWait: true"), 2)
        self.assertIn(
            "- name: librechat-code-interpreter-shared\n"
            "      namespace: librechat-code-interpreter",
            package_release,
        )
        self.assertIn(
            "- name: librechat-code-interpreter-valkey\n"
            "      namespace: librechat-code-interpreter",
            package_release,
        )
        self.assertIn(
            "- name: librechat-code-interpreter-package-init\n"
            "      namespace: librechat-code-interpreter",
            worker_release,
        )


if __name__ == "__main__":
    unittest.main()
