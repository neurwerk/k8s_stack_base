"""Static contracts for the foundational secret-management controllers."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def render(chart: str, namespace: str, *set_values: str) -> str:
    """Render one chart with its committed dependencies and defaults."""
    command = [
        "helm",
        "template",
        namespace,
        str(ROOT / chart),
        "--namespace",
        namespace,
        "--skip-tests",
    ]
    for value in set_values:
        command.extend(["--set", value])
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Helm render failed for {chart}:\n{result.stderr}")
    return result.stdout


def resource(manifest: str, kind: str, name: str) -> str:
    """Return a rendered resource by kind and metadata name."""
    for document in re.split(r"(?m)^---\s*$", manifest):
        if f"kind: {kind}\n" in document and re.search(
            rf"(?m)^  name: {re.escape(name)}$", document
        ):
            return document
    raise AssertionError(f"Missing {kind} {name}")


class SecretFoundationTests(unittest.TestCase):
    """Enforce security and ownership choices across foundation charts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.openbao = render("charts/openbao", "infra-openbao")
        cls.openbao_with_api_endpoint = render(
            "charts/openbao",
            "infra-openbao",
            "infraOpenbaoWrapper.kubernetesApi.endpoint.address=192.0.2.10",
        )
        cls.external_secrets = render(
            "charts/external-secrets", "infra-external-secrets"
        )
        cls.trust_manager = render("charts/trust-manager", "infra-trust-manager")
        cls.reloader = render("charts/reloader", "infra-reloader")

    def test_openbao_is_single_replica_raft_with_native_static_seal(self) -> None:
        self.assertIn("replicas: 1", self.openbao)
        self.assertIn('storage "raft"', self.openbao)
        self.assertIn('seal "static"', self.openbao)
        self.assertRegex(
            self.openbao, r'current_key\s*= "file:///openbao/seal/key"'
        )
        self.assertNotIn("tls_disable", self.openbao)
        self.assertIn("secretName: infra-openbao-static-seal-secret", self.openbao)
        self.assertNotRegex(
            self.openbao,
            r"(?s)kind: Secret\nmetadata:\n\s+name: infra-openbao-static-seal-secret",
        )

    def test_openbao_operator_has_no_ambient_kubernetes_token(self) -> None:
        operator = (ROOT / "releases/openbao/operator.yaml").read_text(encoding="utf-8")
        self.assertIn("name: secret-operator", operator)
        self.assertIn("namespace: infra-openbao", operator)
        self.assertIn("automountServiceAccountToken: false", operator)

    def test_openbao_storage_tls_and_bootstrap_probe_contract(self) -> None:
        self.assertIn("storageClassName: infra-rook-ceph-rbd-openbao", self.openbao)
        self.assertIn("storage: 10Gi", self.openbao)
        self.assertIn("whenDeleted: Retain", self.openbao)
        self.assertIn("whenScaled: Retain", self.openbao)
        self.assertIn("secretName: infra-openbao-tls-secret", self.openbao)
        self.assertIn("- localhost", self.openbao)
        self.assertIn("- 127.0.0.1", self.openbao)
        self.assertIn("infra-openbao-0.infra-openbao-internal.infra-openbao.svc", self.openbao)
        self.assertEqual(
            self.openbao.count(
                "/v1/sys/health?standbyok=true&sealedcode=204&uninitcode=204"
            ),
            2,
        )
        stateful_set = resource(self.openbao, "StatefulSet", "infra-openbao")
        self.assertRegex(
            stateful_set,
            r"(?s)livenessProbe:.*?failureThreshold: 60.*?timeoutSeconds: 5",
        )
        self.assertIn("name: kubernetes-api-connectivity", stateful_set)
        self.assertIn("nc -z -w 5", stateful_set)
        self.assertIn("KUBERNETES_SERVICE_HOST", stateful_set)
        config = resource(self.openbao, "ConfigMap", "infra-openbao-config")
        self.assertRegex(config, r'address\s*= "127\.0\.0\.1:8203"')
        self.assertEqual(
            config.count("disable_unauthed_generate_root_endpoints = false"),
            1,
        )
        self.assertNotIn("containerPort: 8203", stateful_set)
        self.assertNotIn("port: 8203", self.openbao)

    def test_openbao_rbac_network_and_monitoring_are_restricted(self) -> None:
        self.assertRegex(
            self.openbao,
            r"(?s)name: infra-openbao-server-binding.*?name: system:auth-delegator.*?name: infra-openbao.*?namespace: infra-openbao",
        )
        self.assertIn("cidr: 10.43.0.1/32", self.openbao)
        self.assertIn("cidr: 192.0.2.10/32", self.openbao_with_api_endpoint)
        self.assertIn("port: 6443", self.openbao_with_api_endpoint)
        self.assertNotIn("cidr: 0.0.0.0/0", self.openbao)
        self.assertRegex(self.openbao, r"(?m)^kind: ServiceMonitor$")
        self.assertRegex(self.openbao, r"(?m)^kind: PrometheusRule$")
        self.assertIn("alert: OpenBaoSealed", self.openbao)
        self.assertIn("alert: OpenBaoNoLeader", self.openbao)
        self.assertNotIn("alert: OpenBaoSnapshotStale", self.openbao)

    def test_external_secrets_uses_crds_without_delegated_auth_or_bitwarden(self) -> None:
        self.assertRegex(self.external_secrets, r"(?m)^kind: CustomResourceDefinition$")
        self.assertIn(
            "ghcr.io/external-secrets/external-secrets:v2.9.0",
            self.external_secrets,
        )
        self.assertNotIn("system:auth-delegator", self.external_secrets)
        self.assertNotIn("bitwarden-sdk-server", self.external_secrets)

    def test_trust_manager_distributes_only_the_internal_ca_to_labeled_namespaces(self) -> None:
        self.assertRegex(self.trust_manager, r"(?m)^kind: Bundle$")
        self.assertIn("name: infra-openbao-ca-bundle", self.trust_manager)
        self.assertIn("name: infra-cert-manager-internal-ca-secret", self.trust_manager)
        self.assertIn('secrets.neurwerk.com/openbao-trust: "true"', self.trust_manager)
        self.assertNotIn("route53", self.trust_manager.lower())

    def test_trust_manager_crd_is_installed_before_bundle_templates(self) -> None:
        crd = ROOT / "charts/trust-manager/crds/bundles.trust.cert-manager.io.yaml"
        values = (ROOT / "charts/trust-manager/values.yaml").read_text(encoding="utf-8")
        self.assertTrue(crd.is_file())
        self.assertIn("name: bundles.trust.cert-manager.io", crd.read_text(encoding="utf-8"))
        self.assertRegex(values, r"(?s)  crds:\n.*?    enabled: false")

    def test_opensearch_bundle_exposes_only_the_public_ca(self) -> None:
        bundle = resource(
            self.trust_manager, "Bundle", "monitor-opensearch-ca-bundle"
        )
        self.assertIn("name: infra-cert-manager-internal-ca-secret", bundle)
        self.assertIn("key: tls.crt", bundle)
        self.assertIn("configMap:\n      key: ca.crt", bundle)
        self.assertIn(
            "kubernetes.io/metadata.name: infra-cert-manager",
            bundle,
        )
        self.assertNotIn("includeAllKeys", bundle)
        self.assertNotIn("tls.key", bundle)

    def test_reloader_is_limited_to_annotated_workload_namespaces(self) -> None:
        expected_namespaces = {
            "auth-keycloak",
            "auth-keycloak-api-key-bridge",
            "frontend-dify",
            "frontend-librechat",
            "frontend-studio",
            "infra-postgres-auth",
            "infra-postgres-operations",
            "infra-reloader",
            "monitor-fluent-bit",
            "monitor-langfuse",
            "monitor-pii-engine",
            "monitor-agentgateway-extproc",
        }
        resources = re.split(r"(?m)^---\s*$", self.reloader)
        roles = [resource for resource in resources if re.search(r"(?m)^kind: Role$", resource)]
        bindings = [
            resource for resource in resources if re.search(r"(?m)^kind: RoleBinding$", resource)
        ]

        self.assertNotRegex(self.reloader, r"(?m)^kind: ClusterRole(?:Binding)?$")
        self.assertNotIn("infra-reloader-metadata-role", self.reloader)
        self.assertEqual(len(roles), len(expected_namespaces))
        self.assertEqual(len(bindings), len(expected_namespaces))
        self.assertIn("--reload-strategy=annotations", self.reloader)
        self.assertIn("--reload-on-create=true", self.reloader)
        self.assertIn("--sync-after-restart=true", self.reloader)
        self.assertNotIn("--reload-on-delete", self.reloader)
        self.assertNotIn("--auto-reload-all", self.reloader)

        role_namespaces = set()
        binding_namespaces = set()
        for resource in roles:
            namespace = re.search(r"(?m)^  namespace: ([^\n]+)$", resource)
            self.assertIsNotNone(namespace)
            role_namespaces.add(namespace.group(1))

        for resource in bindings:
            namespace = re.search(r"(?m)^  namespace: ([^\n]+)$", resource)
            self.assertIsNotNone(namespace)
            binding_namespaces.add(namespace.group(1))

        runtime = re.search(r"--namespaces=([^\s\"']+)", self.reloader)
        self.assertIsNotNone(runtime)
        runtime_namespaces = set(runtime.group(1).split(","))
        self.assertEqual(role_namespaces, expected_namespaces)
        self.assertEqual(binding_namespaces, expected_namespaces)
        self.assertEqual(runtime_namespaces, expected_namespaces)

        for role in roles:
            self.assertIn("name: infra-reloader-workload-role", role)
            self.assertNotIn('apiGroups: ["batch"]', role)
            self.assertNotIn('"jobs"', role)
            self.assertNotIn('"cronjobs"', role)
            self.assertNotIn('"create"', role)
            self.assertNotIn('"delete"', role)

    def test_foundations_are_not_owned_by_applications_stage(self) -> None:
        aggregate = (ROOT / "releases/applications/kustomization.yaml").read_text(encoding="utf-8")
        for app in ("rook-ceph", "openbao", "external-secrets", "trust-manager", "reloader"):
            self.assertNotRegex(aggregate, rf"(?m)^\s*- {re.escape(app)}\s*$")
            self.assertTrue((ROOT / f"releases/{app}/kustomization.yaml").is_file())

    def test_internal_ca_is_secret_free_and_separate_from_public_issuer(self) -> None:
        public_templates = ROOT / "charts/cert-manager/issuers/templates"
        internal_templates = ROOT / "charts/cert-manager/internal-issuer/templates"
        self.assertEqual(
            {path.name for path in public_templates.glob("*.yaml")},
            {"issuers.yaml", "secret.yaml"},
        )
        internal = "\n".join(
            path.read_text(encoding="utf-8") for path in internal_templates.glob("*.yaml")
        )
        self.assertIn("infra-cert-manager-self-signed-issuer", internal)
        self.assertIn("infra-cert-manager-internal-ca-secret", internal)
        self.assertIn("infra-cert-manager-internal-ca-issuer", internal)
        self.assertNotIn("route53", internal.lower())


if __name__ == "__main__":
    unittest.main()
