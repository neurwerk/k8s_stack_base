"""Validate the actual operator JSON, not only the enclosing ConfigMap."""

import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
# Test-only artifact, never a release pin or a purported published image.
IMAGE = "ghcr.io/neurwerk/k8s-stack-tooling:0.0.0@sha256:" + "a" * 64
VALUES = {
    "maintenance": {
        "enabled": True,
        "image": IMAGE,
        "products": {name: {"enabled": True} for name in ("studio", "dify", "librechat", "langfuse")},
    },
    "authKeycloak": {
        "hostname": "identity.platform.test",
        "realmDisplayName": "Test Company",
        "branding": {"logoConfigMapName": "company-branding", "logoFormat": "png"},
    },
    "frontendStudio": {"studio": {"hostname": "studio.platform.test"}},
    "frontendDify": {"hostname": "dify.platform.test"},
    "frontendLibrechat": {
        "hostname": "chat.platform.test",
        "adminPanel": {"hostname": "chat-admin.platform.test"},
    },
    "monitorLangfuseWrapper": {"hostname": "langfuse.platform.test"},
    "infraAgentgatewayWrapper": {"hostname": "models.platform.test"},
    "infraRookCeph": {"objectStore": {"publicHostname": "storage.platform.test"}},
}
LABELS = {
    "app.kubernetes.io/name": "maintenance",
    "app.kubernetes.io/instance": "maintenance",
    "app.kubernetes.io/part-of": "maintenance",
    "maintenance.neurwerk.com/managed-by": "operator",
}


def render(values):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "values.json"
        path.write_text(json.dumps(values))
        return subprocess.run(
            ["helm", "template", "maintenance", str(ROOT / "charts/maintenance"),
             "--namespace", "maintenance", "--values", str(path)],
            text=True, capture_output=True, check=False,
        )


def contract(result):
    if result.returncode:
        raise AssertionError(result.stderr)
    encoded = re.search(r"^  contract.json: (.*)$", result.stdout, re.MULTILINE)
    return json.loads(json.loads(encoded.group(1)))


class MaintenanceTests(unittest.TestCase):
    def test_disabled_has_only_static_infrastructure(self):
        result = render({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertCountEqual(re.findall(r"^kind: (.*)$", result.stdout, re.MULTILINE),
                              ["Service", "NetworkPolicy"])
        self.assertIn("type: ClusterIP", result.stdout)
        self.assertIn("egress: []", result.stdout)
        self.assertIn("podSelector: {}", result.stdout)
        self.assertIn("kubernetes.io/metadata.name: kube-system", result.stdout)
        self.assertIn("app.kubernetes.io/name: traefik", result.stdout)
        self.assertIn("port: 8080", result.stdout)

    def test_operator_contract_security_routes_and_branding(self):
        for logo_format in ("png", "svg"):
            with self.subTest(logo_format=logo_format):
                values = copy.deepcopy(VALUES)
                values["authKeycloak"]["branding"]["logoFormat"] = logo_format
                result = render(values)
                data = contract(result)
                self.assertCountEqual(re.findall(r"^kind: (.*)$", result.stdout, re.MULTILINE),
                                      ["Service", "NetworkPolicy", "ConfigMap"])
                self.assertEqual(set(data), {"version", "namespace", "serviceName", "servicePort", "deployment", "routes"})
                self.assertEqual((data["version"], data["namespace"], data["serviceName"], data["servicePort"]),
                                 (1, "maintenance", "maintenance", 8080))
                deployment = data["deployment"]
                validation = subprocess.run(
                    ["kubeconform", "-strict", "-summary"], input=json.dumps(deployment),
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)
                self.assertEqual(deployment["apiVersion"], "apps/v1")
                self.assertEqual(deployment["kind"], "Deployment")
                self.assertEqual(deployment["metadata"], {"name": "maintenance", "namespace": "maintenance", "labels": LABELS})
                self.assertEqual(deployment["spec"]["replicas"], 1)
                self.assertEqual(deployment["spec"]["selector"]["matchLabels"], LABELS)
                self.assertEqual(deployment["spec"]["template"]["metadata"], {"labels": LABELS})
                pod = deployment["spec"]["template"]["spec"]
                self.assertFalse(pod["automountServiceAccountToken"])
                self.assertTrue(pod["securityContext"]["runAsNonRoot"])
                self.assertEqual(pod["securityContext"]["seccompProfile"], {"type": "RuntimeDefault"})
                container, = pod["containers"]
                self.assertEqual(container["image"], IMAGE)
                self.assertEqual(container["command"], ["maintenance-server"])
                self.assertEqual(container["ports"], [{"name": "http", "containerPort": 8080}])
                self.assertEqual(container["securityContext"], {
                    "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                })
                self.assertEqual({env["name"]: env["value"] for env in container["env"]}, {
                    "MAINTENANCE_COMPANY_NAME": "Test Company",
                    "MAINTENANCE_LOGO_PATH": f"/branding/company-logo.{logo_format}",
                    "MAINTENANCE_RETRY_AFTER": "300",
                })
                for probe in ("readinessProbe", "livenessProbe"):
                    self.assertEqual(container[probe]["httpGet"], {"path": "/_maintenance/healthz", "port": 8080})
                self.assertEqual(pod["volumes"][0]["configMap"], {
                    "name": "company-branding", "items": [{"key": f"company-logo.{logo_format}", "path": f"company-logo.{logo_format}"}],
                })
                self.assertEqual(container["volumeMounts"][0], {"name": "branding", "mountPath": "/branding", "readOnly": True})
                self.assertEqual(container["volumeMounts"][1]["mountPath"], "/tmp")
                self.assertEqual(set(container["resources"]), {"requests", "limits"})
                self.assertEqual(set(data["routes"]), {"global", "studio", "dify", "librechat", "langfuse"})
                all_hosts = []
                for scope, route in data["routes"].items():
                    self.assertEqual(route["kind"], "IngressRoute")
                    self.assertEqual(route["apiVersion"], "traefik.io/v1alpha1")
                    metadata = route["metadata"]
                    self.assertEqual(set(metadata), {"name", "namespace", "labels", "annotations"})
                    self.assertEqual(metadata["name"], f"maintenance-{scope}")
                    self.assertEqual(metadata["namespace"], "maintenance")
                    self.assertEqual(metadata["labels"], LABELS)
                    hosts = json.loads(metadata["annotations"]["maintenance.neurwerk.com/hosts"])
                    spec = route["spec"]
                    self.assertEqual(set(spec), {"entryPoints", "tls", "routes"})
                    self.assertEqual(spec["entryPoints"], ["websecure"])
                    self.assertEqual(spec["tls"], {})
                    self.assertEqual(spec["routes"], [{
                        "kind": "Rule", "match": " || ".join(f"Host(`{host}`)" for host in hosts),
                        "priority": 2000000000 if scope == "global" else 1900000000,
                        "services": [{"name": "maintenance", "port": 8080}],
                    }])
                    if scope != "global":
                        all_hosts.extend(hosts)
                self.assertEqual(json.loads(data["routes"]["global"]["metadata"]["annotations"]["maintenance.neurwerk.com/hosts"]), all_hosts)
                self.assertEqual(len(set(all_hosts)), 5)

    def test_selection_and_resources(self):
        values = copy.deepcopy(VALUES)
        del values["maintenance"]["image"]
        values["maintenance"]["products"] = {"studio": {"enabled": True}}
        values["maintenance"]["resources"] = {
            "requests": {"cpu": "20m", "memory": "80Mi"}, "limits": {"cpu": "200m", "memory": "160Mi"},
        }
        data = contract(render(values))
        self.assertEqual(set(data["routes"]), {"global", "studio"})
        self.assertRegex(data["deployment"]["spec"]["template"]["spec"]["containers"][0]["image"],
                         r"^ghcr\.io/neurwerk/k8s-stack-tooling:0\.7\.0@sha256:[0-9a-f]{64}$")
        self.assertEqual(data["deployment"]["spec"]["template"]["spec"]["containers"][0]["resources"], values["maintenance"]["resources"])

    def test_reject_invalid_approval_inputs(self):
        cases = [
            ("maintenance.image", value) for value in ("", "registry.test/server:latest", "registry.test/server:1.2.3", "registry.test/server@sha256:" + "a" * 64)
        ] + [
            ("maintenance.image", value + "@sha256:" + "a" * 64) for value in (
                "registry.test/maintenance:1.2.3", "ghcr.io/neurwerk/other:1.2.3",
                "mirror.test/neurwerk/k8s-stack-tooling:1.2.3",
                "ghcr.io/neurwerk/k8s-stack-tooling:01.2.3",
                "ghcr.io/neurwerk/k8s-stack-tooling:1.02.3",
                "ghcr.io/neurwerk/k8s-stack-tooling:1.2.03",
                "ghcr.io/neurwerk/k8s-stack-tooling:1.2.3-rc.1",
                "ghcr.io/neurwerk/k8s-stack-tooling:1.2.3+build",
            )
        ] + [
            ("frontendDify.hostname", value) for value in (
                "", "*.platform.test", "dify.example.com", "placeholder.platform.test",
                "https://dify.platform.test", "dify.platform.test/path", "studio.platform.test",
                "identity.platform.test", "models.platform.test", "storage.platform.test",
                "a" * 64 + ".platform.test", "dify.platform.test`)", "dify.place.holder",
                "192.0.2.1", "dify.platform.123", "dify.platform.1test",
            )
        ] + [
            ("maintenance.products.keycloak", {"enabled": True}),
            ("maintenance.products.studio.hosts", ["unknown.platform.test"]),
            ("maintenance.products.studio.enabled", "true"),
            ("authKeycloak.branding.logoConfigMapName", ""),
            ("authKeycloak.branding.logoFormat", "jpeg"),
            ("authKeycloak.realmDisplayName", " "),
            ("maintenance.products", {name: {"enabled": False} for name in VALUES["maintenance"]["products"]}),
        ]
        for path, value in cases:
            with self.subTest(path=path, value=value):
                values = copy.deepcopy(VALUES)
                parent = values
                parts = path.split(".")
                for part in parts[:-1]:
                    parent = parent[part]
                parent[parts[-1]] = value
                result = render(values)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_traefik_explicit_crd_defaults(self):
        result = subprocess.run(["helm", "template", "traefik", str(ROOT / "charts/traefik")],
                                text=True, capture_output=True, check=True)
        self.assertIn("kubernetesCRD:\n        enabled: true\n        allowEmptyServices: true", result.stdout)
        self.assertIn("kubernetesGateway:\n        enabled: true", result.stdout)


if __name__ == "__main__":
    unittest.main()
