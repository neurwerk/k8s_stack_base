"""Validate the actual operator JSON, not only the enclosing ConfigMap."""

import copy
import json
import subprocess
import unittest
from helm import render as helm_render, resource

# Test-only artifact, never a release pin or a purported published image.
IMAGE = "ghcr.io/neurwerk/k8s-stack-tooling:0.0.0@sha256:" + "a" * 64
VALUES = {
    "maintenance": {
        "enabled": True,
        "image": IMAGE,
        "products": {
            name: {"enabled": True} for name in ("studio", "dify", "librechat", "langfuse")
        },
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
def render(values):
    return helm_render(
        "maintenance",
        values,
        release="maintenance",
        namespace="maintenance",
        value_files=(),
        check=False,
    )


def contract(result):
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(resource(result, "ConfigMap")["data"]["contract.json"])


class MaintenanceTests(unittest.TestCase):
    def test_disabled_has_only_static_infrastructure(self):
        result = render({})
        self.assertEqual(result.returncode, 0, result.stderr)
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
                deployment = data["deployment"]
                validation = subprocess.run(
                    ["kubeconform", "-strict", "-summary"], input=json.dumps(deployment),
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)
                pod = deployment["spec"]["template"]["spec"]
                self.assertFalse(pod["automountServiceAccountToken"])
                self.assertTrue(pod["securityContext"]["runAsNonRoot"])
                self.assertEqual(pod["securityContext"]["seccompProfile"], {"type": "RuntimeDefault"})
                container, = pod["containers"]
                self.assertEqual(container["image"], IMAGE)
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
                all_hosts = []
                for scope, route in data["routes"].items():
                    self.assertEqual(route["kind"], "IngressRoute")
                    self.assertEqual(route["apiVersion"], "traefik.io/v1alpha1")
                    metadata = route["metadata"]
                    self.assertEqual(metadata["name"], f"maintenance-{scope}")
                    self.assertEqual(metadata["namespace"], "maintenance")
                    hosts = json.loads(metadata["annotations"]["maintenance.neurwerk.com/hosts"])
                    spec = route["spec"]
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

    def test_selection_and_resources(self):
        values = copy.deepcopy(VALUES)
        del values["maintenance"]["image"]
        values["maintenance"]["products"] = {"studio": {"enabled": True}}
        values["maintenance"]["resources"] = {
            "requests": {"cpu": "20m", "memory": "80Mi"}, "limits": {"cpu": "200m", "memory": "160Mi"},
        }
        data = contract(render(values))
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

if __name__ == "__main__":
    unittest.main()
