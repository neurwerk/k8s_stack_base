"""Exercise scoped addon approval and the actual enabled configuration Job."""

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from helm import ROOT, documents, render, resource


CHART = "keycloak/addon-oidc"
# Synthetic, test-only digest. This is NOT publication evidence or a deployment pin.
TEST_ONLY_UNPUBLISHED_IMAGE = "ghcr.io/neurwerk/k8s-stack-tooling:0.11.1@sha256:" + "0" * 64


def configuration(client="example-addon"):
    return {
        "enabled": True,
        "authentication": {
            "clientId": client,
            "redirectUris": [f"https://{client}.example.com/callback"],
            "webOrigins": [f"https://{client}.example.com"],
            "realmRoles": [f"{client}-user"],
        },
        "clientApproval": {"clientId": client, "realmRoles": [f"{client}-user"]},
    }


class AddonOidcTests(unittest.TestCase):
    def test_platform_clients_are_reserved_but_application_clients_are_allowed(self):
        for client in (
            "agentgateway",
            "studio",
            "keycloak-api-key-bridge",
            "account",
            "account-console",
            "admin-cli",
            "broker",
            "realm-management",
            "security-admin-console",
        ):
            with self.subTest(client=client):
                result = self.scope(configuration(client), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("reserved authentication client ID", result.stderr)
        values = configuration("custom-studio")
        values["authKeycloak"] = {"studioClientId": "custom-studio"}
        self.assertIn("reserved authentication client ID", self.scope(values, check=False).stderr)
        for client in ("sample", "second-app", "example-addon"):
            with self.subTest(client=client):
                self.scope(configuration(client))

    def test_enabled_job_wires_approved_scopes_and_local_credentials(self):
        for gateway in (False, True):
            with self.subTest(gateway=gateway):
                values = configuration()
                values.update(
                    k8sTools={"image": TEST_ONLY_UNPUBLISHED_IMAGE},
                    authKeycloak={"realm": "example", "adminUser": "setup-admin"},
                    clientSecret={"name": "example-oidc", "key": "clientSecret"},
                )
                auth = values["authentication"]
                auth.update(roleClaim="example_roles", refreshTokenRotation=not gateway)
                if gateway:
                    auth.update(
                        mode="login+gateway",
                        userTokenForwarding=True,
                        audiences=["agentgateway"],
                        gatewayPermissions=["llm:invoke", "model:vendor/Model-1.2:invoke"],
                    )
                    values["clientApproval"].update(
                        audiences=["agentgateway"], gatewayPermissions=["llm:invoke"]
                    )
                result = render(CHART, values, namespace="auth-keycloak", value_files=())
                schema = subprocess.run(
                    ["kubeconform", "-strict", "-summary"],
                    input=result.stdout, capture_output=True, text=True, check=False,
                )
                self.assertEqual(schema.returncode, 0, schema.stdout + schema.stderr)
                with tempfile.TemporaryDirectory() as directory:
                    manifest = Path(directory) / "test-only-render.yaml"
                    manifest.write_text(result.stdout)
                    lint = subprocess.run(
                        ["kube-linter", "lint", "--config", str(ROOT / ".kube-linter.yaml"), str(manifest)],
                        capture_output=True, text=True, check=False,
                    )
                self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
                self.assertEqual({d["kind"] for d in documents(result)}, {"Job", "NetworkPolicy"})
                self.assertTrue(
                    all(d["metadata"]["namespace"] == "auth-keycloak" for d in documents(result))
                )
                job = resource(result, "Job")
                template = job["spec"]["template"]
                self.assertEqual(
                    template["metadata"]["labels"]["app.kubernetes.io/component"], "configuration"
                )
                pod = template["spec"]
                self.assertFalse(pod["automountServiceAccountToken"])
                self.assertEqual(
                    pod["securityContext"],
                    {
                        "runAsNonRoot": True,
                        "runAsUser": 1000,
                        "runAsGroup": 1000,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                )
                self.assertEqual(len(pod["containers"]), 1)
                container = pod["containers"][0]
                self.assertEqual(container["image"], TEST_ONLY_UNPUBLISHED_IMAGE)
                self.assertEqual(container["command"], ["upsert-scoped-oidc-client"])
                self.assertEqual(
                    container["securityContext"],
                    {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                )
                env = {e["name"]: e for e in container["env"]}
                for name, expected in {
                    "KC_CLIENT_ID": "example-addon",
                    "KC_REALM": "example",
                    "KC_ADMIN_USER": "setup-admin",
                    "KC_ROLE_CLAIM": "example_roles",
                    "KC_INTERNAL_URL": "http://auth-keycloak-keycloak-service.auth-keycloak.svc.cluster.local",
                    "KC_REFRESH_TOKEN_ROTATION_ENABLED": "false" if gateway else "true",
                }.items():
                    self.assertEqual(env[name]["value"], expected)
                for name, expected in {
                    "KC_REDIRECT_URIS": auth["redirectUris"],
                    "KC_WEB_ORIGINS": auth["webOrigins"],
                    "KC_REALM_ROLE_SCOPE": auth["realmRoles"],
                    "KC_CLIENT_AUDIENCES": ["agentgateway"] if gateway else [],
                    "KC_GATEWAY_ROLE_SCOPE": ["llm:invoke"] if gateway else [],
                }.items():
                    self.assertEqual(json.loads(env[name]["value"]), expected)
                secrets = {
                    name: entry["valueFrom"] for name, entry in env.items() if "valueFrom" in entry
                }
                self.assertEqual(
                    secrets,
                    {
                        "KC_ADMIN_PASSWORD": {
                            "secretKeyRef": {"name": "auth-keycloak-secret", "key": "adminPassword"}
                        },
                        "KC_CLIENT_SECRET": {
                            "secretKeyRef": {"name": "example-oidc", "key": "clientSecret"}
                        },
                    },
                )
                policy = resource(result, "NetworkPolicy")["spec"]
                self.assertEqual(policy["policyTypes"], ["Ingress", "Egress"])
                self.assertEqual(policy["ingress"], [])
                for key, value in policy["podSelector"]["matchLabels"].items():
                    self.assertEqual(template["metadata"]["labels"][key], value)
                self.assertEqual(
                    policy["egress"],
                    [
                        {
                            "to": [
                                {
                                    "namespaceSelector": {
                                        "matchLabels": {
                                            "kubernetes.io/metadata.name": "kube-system"
                                        }
                                    },
                                    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                                }
                            ],
                            "ports": [
                                {"protocol": "UDP", "port": 53},
                                {"protocol": "TCP", "port": 53},
                            ],
                        },
                        {
                            "to": [
                                {
                                    "podSelector": {
                                        "matchLabels": {"app": "auth-keycloak-keycloak-app"}
                                    }
                                }
                            ],
                            "ports": [{"protocol": "TCP", "port": 8080}],
                        },
                    ],
                )

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.scope_chart = Path(cls.directory.name)
        (cls.scope_chart / "templates").mkdir()
        source = ROOT / "charts" / CHART
        for filename in (
            "Chart.yaml",
            "values.yaml",
            "values.schema.json",
            "templates/_helpers.tpl",
        ):
            shutil.copyfile(source / filename, cls.scope_chart / filename)
        # Invoke the production approval functions, independently of the publication gate.
        (cls.scope_chart / "templates/scope.yaml").write_text(
            '{{ include "addon-oidc.validateAccess" . }}\n'
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: scope\ndata:\n"
            '  gateway: {{ include "addon-oidc.gatewayScope" . | quote }}\n'
        )

    def scope(self, values, **kwargs):
        return render(self.scope_chart, values, namespace="auth-keycloak", value_files=(), **kwargs)

    def test_disabled_chart_and_missing_or_old_runtime_cannot_create_jobs(self):
        self.assertEqual(documents(render(CHART, value_files=())), [])
        values = configuration()
        values["k8sTools"] = {"image": ""}
        result = render(CHART, values, namespace="auth-keycloak", value_files=(), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("published immutable tooling image", result.stderr)
        # This is an existing real pin, deliberately too old for the new contract.
        old = yaml.safe_load((ROOT / "charts/keycloak/addon-access/values.yaml").read_text())
        values["k8sTools"] = old["k8sTools"]
        result = render(CHART, values, namespace="auth-keycloak", value_files=(), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tooling >=0.11.1", result.stderr)

    def test_login_and_two_gateway_addons_require_only_configuration(self):
        for client in ("example-addon", "second-addon"):
            with self.subTest(client=client):
                values = configuration(client)
                self.assertEqual(
                    json.loads(resource(self.scope(values), "ConfigMap")["data"]["gateway"]), []
                )
                request = values["authentication"]
                request.update(
                    mode="login+gateway",
                    userTokenForwarding=True,
                    audiences=["agentgateway"],
                    gatewayPermissions=["llm:invoke", "model:vendor/Model-1.2:invoke"],
                )
                values["clientApproval"]["audiences"] = ["agentgateway"]
                for approved, expected in (
                    ([], []),
                    (["llm:invoke", "model:other:invoke"], ["llm:invoke"]),
                    (request["gatewayPermissions"], request["gatewayPermissions"]),
                ):
                    values["clientApproval"]["gatewayPermissions"] = approved
                    self.assertEqual(
                        json.loads(resource(self.scope(values), "ConfigMap")["data"]["gateway"]),
                        expected,
                    )

    def test_unapproved_or_unsafe_configuration_is_rejected(self):
        changes = [
            ("clientApproval", {"clientId": "other"}),
            ("clientApproval", {"realmRoles": []}),
            ("authentication", {"audiences": ["other-api"]}),
            ("authentication", {"mode": "login+gateway", "audiences": ["agentgateway"]}),
            ("authentication", {"gatewayPermissions": ["llm:invoke"]}),
            ("authentication", {"roleClaim": "sub"}),
            ("authentication", {"redirectUris": ["https://example.com/*"]}),
            ("authentication", {"webOrigins": ["https://example.com/"]}),
        ]
        for field, change in changes:
            with self.subTest(field=field, change=change):
                values = copy.deepcopy(configuration())
                values[field].update(change)
                self.assertNotEqual(self.scope(values, check=False).returncode, 0)
        result = render(
            self.scope_chart, configuration(), namespace="application", value_files=(), check=False
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must run in auth-keycloak", result.stderr)
