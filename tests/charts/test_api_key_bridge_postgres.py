"""Guard the opt-in PostgreSQL cutover and the default SQLite deployment."""

import unittest
from pathlib import Path

import yaml

from helm import documents, render, resource


class BridgePostgresTests(unittest.TestCase):
    def test_old_image_cannot_enable_postgres(self):
        result = render("keycloak-api-key-bridge", {
            "authKeycloakApiKeyBridge": {"postgres": {"enabled": True}},
        }, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires a verified PostgreSQL-compatible image pin", result.stderr)

        unpinned = render("keycloak-api-key-bridge", {
            "authKeycloakApiKeyBridge": {
                "bridgeImage": "example.invalid/bridge:postgres-compatible-test",
                "postgres": {"enabled": True},
            },
        }, check=False)
        self.assertNotEqual(unpinned.returncode, 0)
        self.assertIn("requires a verified PostgreSQL-compatible image pin", unpinned.stderr)

    def test_cutover_excludes_sqlite_and_wires_secret_and_policy(self):
        values = {"authKeycloakApiKeyBridge": {
            "bridgeImage": "example.invalid/bridge:postgres-compatible-test@sha256:" + "a" * 64,
            "postgres": {"enabled": True},
        }}
        new = render("keycloak-api-key-bridge", values,
                     namespace="auth-keycloak-api-key-bridge", release="auth-keycloak-api-key-bridge")
        old = render("keycloak-api-key-bridge", namespace="auth-keycloak-api-key-bridge",
                     release="auth-keycloak-api-key-bridge")
        self.assertFalse(any(doc["kind"] == "PersistentVolumeClaim" for doc in documents(new)))
        self.assertTrue(any(doc["kind"] == "PersistentVolumeClaim" for doc in documents(old)))
        pod = resource(new, "Deployment")["spec"]["template"]["spec"]
        old_pod = resource(old, "Deployment")["spec"]["template"]["spec"]
        self.assertNotIn("initContainers", old_pod)
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
        self.assertNotIn("data", [volume["name"] for volume in pod["volumes"]])
        self.assertIn("data", [volume["name"] for volume in old_pod["volumes"]])
        env = {item["name"]: item for item in pod["containers"][0]["env"]}
        self.assertNotIn("KEYCLOAK_API_KEY_BRIDGE_DATABASE_URL", env)
        self.assertEqual(env["KEYCLOAK_API_KEY_BRIDGE_POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"],
                         {"name": "auth-keycloak-api-key-bridge-postgres-secret", "key": "password"})
        policy = resource(new, "NetworkPolicy", "auth-keycloak-api-key-bridge-network-policy")
        self.assertIn(9712, [p["port"] for rule in policy["spec"]["egress"] for p in rule["ports"]])

    def test_provisioning_is_gated_and_separate_from_statefulset_secret(self):
        enabled = render("postgres/operations", {"apiKeyBridge": {"enabled": True}},
                         namespace="infra-postgres-operations", release="postgres-operations")
        disabled = render("postgres/operations", namespace="infra-postgres-operations",
                          release="postgres-operations")
        job = resource(enabled, "Job", "postgres-operations-provision")
        script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
        self.assertIn("Existing API key bridge database lacks the provisioner marker", script)
        self.assertIn("CREATE DATABASE api_key_bridge OWNER api_key_bridge", script)
        self.assertIn("verify_isolation api_key_bridge", script)
        self.assertNotIn("CREATE DATABASE api_key_bridge", resource(disabled, "Job")["spec"]["template"]["spec"]["containers"][0]["args"][0])
        env = job["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertEqual(next(e for e in env if e["name"] == "API_KEY_BRIDGE_PASSWORD")["valueFrom"]["secretKeyRef"]["name"],
                         "api-key-bridge-postgres-values")
        ingress = resource(enabled, "NetworkPolicy", "postgres-operations-ingress")
        self.assertIn("auth-keycloak-api-key-bridge", str(ingress["spec"]["ingress"][0]["from"]))
        self.assertNotIn("auth-keycloak-api-key-bridge", str(resource(disabled, "NetworkPolicy", "postgres-operations-ingress")))
        root = Path(__file__).resolve().parents[2]
        secrets = list(yaml.safe_load_all((root / "releases/keycloak-api-key-bridge/secret-sync/postgres.yaml").read_text()))
        self.assertEqual([(d["spec"]["data"][0]["remoteRef"]["key"], d["spec"]["data"][0]["remoteRef"]["property"]) for d in secrets], [
            ("infra-postgres-operations/internal", "apiKeyBridgePassword"),
            ("auth-keycloak-api-key-bridge/internal", "postgresqlPassword"),
        ])


if __name__ == "__main__":
    unittest.main()
