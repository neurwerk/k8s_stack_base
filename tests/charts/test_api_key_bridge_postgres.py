"""Guard the required PostgreSQL bridge and absence of SQLite resources."""

import unittest
from pathlib import Path

import yaml

from helm import documents, render, resource


class BridgePostgresTests(unittest.TestCase):
    def test_only_verified_bridge_image_is_accepted(self):
        result = render("keycloak-api-key-bridge", {
            "authKeycloakApiKeyBridge": {"bridgeImage": "ghcr.io/neurwerk/k8s-stack-keycloak-api-key-bridge:0.7.1@sha256:" + "7" * 64},
        }, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires the verified PostgreSQL-compatible 0.8.1 image pin", result.stderr)

        unpinned = render("keycloak-api-key-bridge", {
            "authKeycloakApiKeyBridge": {
                "bridgeImage": "example.invalid/bridge:postgres-compatible-test",
            },
        }, check=False)
        self.assertNotEqual(unpinned.returncode, 0)
        self.assertIn("requires the verified PostgreSQL-compatible 0.8.1 image pin", unpinned.stderr)

    def test_default_excludes_sqlite_and_wires_secret_and_policy(self):
        new = render("keycloak-api-key-bridge",
                      namespace="auth-keycloak-api-key-bridge", release="auth-keycloak-api-key-bridge")
        self.assertFalse(any(doc["kind"] == "PersistentVolumeClaim" for doc in documents(new)))
        pod = resource(new, "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(len(pod["initContainers"]), 1)
        init = pod["initContainers"][0]
        self.assertEqual(init["name"], "keycloak-api-key-bridge-init-db")
        self.assertEqual(init["command"], ["keycloak-api-key-bridge-init-db"])
        self.assertEqual(init["image"], pod["containers"][0]["image"])
        self.assertEqual(init["securityContext"]["readOnlyRootFilesystem"], True)
        self.assertEqual(
            {e["name"]: e for e in init["env"]},
            {e["name"]: e for e in pod["containers"][0]["env"] if e["name"].startswith("KEYCLOAK_API_KEY_BRIDGE_POSTGRES_")},
        )
        self.assertNotIn("data", [volume["name"] for volume in pod.get("volumes", [])])
        env = {item["name"]: item for item in pod["containers"][0]["env"]}
        self.assertNotIn("KEYCLOAK_API_KEY_BRIDGE_DATABASE_URL", env)
        self.assertEqual(env["KEYCLOAK_API_KEY_BRIDGE_POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"],
                         {"name": "auth-keycloak-api-key-bridge-postgres-secret", "key": "password"})
        policy = resource(new, "NetworkPolicy", "auth-keycloak-api-key-bridge-network-policy")
        self.assertIn(9712, [p["port"] for rule in policy["spec"]["egress"] for p in rule["ports"]])

    def test_legacy_sqlite_values_fail_closed(self):
        for value in ({"postgres": {"enabled": False}}, {"persistence": {"size": "1Gi"}},
                      {"config": {"database_url": "sqlite:////data/api_keys.db"}}):
            with self.subTest(value=value):
                result = render("keycloak-api-key-bridge", {"authKeycloakApiKeyBridge": value}, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("PostgreSQL-only", result.stderr)

    def test_managed_registrations_mount_owned_files(self):
        empty = render("keycloak-api-key-bridge")
        self.assertFalse(any(doc["kind"] == "ConfigMap" for doc in documents(empty)))
        empty_pod = resource(empty, "Deployment")["spec"]["template"]["spec"]
        self.assertNotIn("KEYCLOAK_API_KEY_BRIDGE_MANAGED_REGISTRATIONS",
                         [e["name"] for e in empty_pod["containers"][0]["env"]])
        self.assertFalse(any(v["name"].startswith("managed-") for v in empty_pod.get("volumes", [])))

        configured = render("keycloak-api-key-bridge", {"authKeycloakApiKeyBridge": {
            "managedRegistrations": [{"grantConfigMap": "addon-grants", "grantKey": "key.json",
                                       "verifierSecret": "addon-verifiers", "verifierKey": "key.sha256"}],
        }})
        pod = resource(configured, "Deployment")["spec"]["template"]["spec"]
        env = {entry["name"]: entry for entry in pod["containers"][0]["env"]}
        self.assertEqual(env["KEYCLOAK_API_KEY_BRIDGE_MANAGED_REGISTRATIONS"]["value"],
                         '[{"grant_file":"/var/run/managed-api-key-grants/0.json","verifier_file":"/var/run/managed-api-key-verifiers/0.sha256"}]')
        self.assertEqual({volume["name"] for volume in pod["volumes"]}, {"managed-grants", "managed-verifiers"})

    def test_provisioning_is_required_and_separate_from_statefulset_secret(self):
        enabled = render("postgres/operations",
                         namespace="infra-postgres-operations", release="postgres-operations")
        job = resource(enabled, "Job", "postgres-operations-provision")
        script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
        self.assertIn("Existing API key bridge database lacks the provisioner marker", script)
        self.assertIn("CREATE DATABASE api_key_bridge OWNER api_key_bridge", script)
        self.assertIn("verify_isolation api_key_bridge", script)
        env = job["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertEqual(next(e for e in env if e["name"] == "API_KEY_BRIDGE_PASSWORD")["valueFrom"]["secretKeyRef"]["name"],
                         "api-key-bridge-postgres-values")
        ingress = resource(enabled, "NetworkPolicy", "postgres-operations-ingress")
        self.assertIn("auth-keycloak-api-key-bridge", str(ingress["spec"]["ingress"][0]["from"]))
        disabled = render("postgres/operations", {"apiKeyBridge": {"enabled": False}}, check=False)
        self.assertNotEqual(disabled.returncode, 0)
        self.assertIn("required for this Base release", disabled.stderr)
        root = Path(__file__).resolve().parents[2]
        secrets = list(yaml.safe_load_all((root / "releases/keycloak-api-key-bridge/secret-sync/postgres.yaml").read_text()))
        self.assertEqual([(d["spec"]["data"][0]["remoteRef"]["key"], d["spec"]["data"][0]["remoteRef"]["property"]) for d in secrets], [
            ("infra-postgres-operations/internal", "apiKeyBridgePassword"),
            ("auth-keycloak-api-key-bridge/internal", "postgresqlPassword"),
        ])


if __name__ == "__main__":
    unittest.main()
