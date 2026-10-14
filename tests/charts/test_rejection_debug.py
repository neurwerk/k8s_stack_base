"""Security boundaries for opt-in rejected content export (offline)."""

import unittest
import subprocess
from pathlib import Path

from helm import documents, render, resource


PREFIX = "EXTPROC_REJECTION_LANGFUSE__"
SECRET = "monitor-agentgateway-extproc-rejection-debug"
IDENTITY = "monitor-agentgateway-extproc-rejection-debug"


def extproc(values):
    return render("agentgateway-extproc", values, namespace="monitor-agentgateway-extproc")


def langfuse(values):
    return render("langfuse", values, namespace="monitor-langfuse", release="monitor-langfuse")


def peer_rules(result, policy, direction, namespace):
    rules = resource(result, "NetworkPolicy", policy)["spec"][direction]
    return [rule for rule in rules if any(
        peer.get("namespaceSelector", {}).get("matchLabels", {}).get(
            "kubernetes.io/metadata.name"
        ) == namespace
        for peer in rule.get("to" if direction == "egress" else "from", [])
    )]


class RejectionDebugTests(unittest.TestCase):
    def test_initializer_security_and_supervision(self):
        subprocess.run(["node", "--test", str(Path(__file__).with_name("rejection-debug-init.test.cjs"))],
                       check=True, timeout=30)

    def test_capture_stop_keeps_lifecycle_credentials(self):
        values = {"rejectedRequestDebug": {"enabled": True, "captureEnabled": False}}
        result = extproc(values)
        pod = resource(result, "Deployment")["spec"]["template"]["spec"]
        self.assertFalse(any(item["name"].startswith(PREFIX) for item in pod["containers"][0]["env"]))
        resource(result, "ExternalSecret", SECRET)
        resource(langfuse(values), "Job", "monitor-langfuse-rejection-debug-init-job")
        resource(langfuse(values), "CronJob", "monitor-langfuse-rejection-debug-cleanup")

    def test_cleanup_has_only_debug_keys_no_token_and_narrow_network(self):
        result = langfuse({"rejectedRequestDebug": {"enabled": True}})
        job = resource(result, "CronJob", "monitor-langfuse-rejection-debug-cleanup")["spec"]
        self.assertEqual(job["concurrencyPolicy"], "Forbid")
        self.assertGreater(job["jobTemplate"]["spec"]["activeDeadlineSeconds"], 600)
        pod = job["jobTemplate"]["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        account = resource(result, "ServiceAccount", pod["serviceAccountName"])
        self.assertFalse(account["automountServiceAccountToken"])
        container = pod["containers"][0]
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        refs = [item["valueFrom"]["secretKeyRef"] for item in container["env"] if "valueFrom" in item]
        self.assertEqual(refs, [
            {"name": "monitor-langfuse-rejection-debug", "key": "public-key"},
            {"name": "monitor-langfuse-rejection-debug", "key": "secret-key"},
        ])
        policy = resource(result, "NetworkPolicy", "monitor-langfuse-rejection-debug-cleanup-network-policy")["spec"]
        self.assertEqual(policy["ingress"], [])
        self.assertEqual(len(policy["egress"]), 2)
        self.assertEqual(policy["egress"][1], {
            "to": [{"podSelector": {"matchLabels": {
                "app.kubernetes.io/name": "langfuse", "app.kubernetes.io/instance": "monitor-langfuse", "app": "web",
            }}}], "ports": [{"port": 3000, "protocol": "TCP"}],
        })

    def test_initializer_not_selected_by_services_and_uses_disabled_migrations(self):
        result = langfuse({"rejectedRequestDebug": {"enabled": True}})
        job = resource(result, "Job", "monitor-langfuse-rejection-debug-init-job")
        labels = job["spec"]["template"]["metadata"]["labels"]
        for doc in documents(result):
            if doc["kind"] == "Service" and doc["spec"].get("selector"):
                self.assertFalse(all(labels.get(key) == value for key, value in doc["spec"]["selector"].items()))
        pod = job["spec"]["template"]["spec"]
        env = {item["name"]: item for item in pod["containers"][0]["env"]}
        for name in ("LANGFUSE_AUTO_POSTGRES_MIGRATION_DISABLED", "LANGFUSE_AUTO_CLICKHOUSE_MIGRATION_DISABLED"):
            self.assertEqual(env[name]["value"], "true")
        self.assertNotIn("LANGFUSE_INIT_PROJECT_RETENTION", env)
        self.assertFalse(pod["automountServiceAccountToken"])

    def test_cleanup_worker_prerequisites_fail_closed(self):
        for name, value in (("LANGFUSE_ENABLE_BLOB_STORAGE_FILE_LOG", "false"),
                            ("LANGFUSE_MIGRATION_V4_ALLOW_PREVIEW_OPT_IN", "false"),
                            ("DELETE_SKIP_PROJECT_IDS", "rejected-request-debug")):
            with self.subTest(name=name):
                result = render("langfuse", {
                    "rejectedRequestDebug": {"enabled": True},
                    "langfuse": {"langfuse": {"worker": {"pod": {"additionalEnv": [{"name": name, "value": value}]}}}},
                }, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("rejectedRequestDebug requires", result.stderr)

    def test_imported_worker_env_requires_validated_explicit_deletion_exclusions(self):
        for scope in ("common", "worker"):
            with self.subTest(scope=scope):
                worker = {"additionalEnv": []}
                settings = {"worker": {"pod": worker}}
                target = settings if scope == "common" else worker
                target["additionalEnvFrom"] = [{"configMapRef": {"name": "imported-worker-settings"}}]
                values = {"rejectedRequestDebug": {"enabled": True}, "langfuse": {"langfuse": settings}}
                result = render("langfuse", values, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("explicit string DELETE_SKIP_PROJECT_IDS", result.stderr)

                # Explicit env takes precedence over envFrom, including an empty
                # value. Preserve allowed unrelated exclusions rather than erase them.
                for exclusions in ("", "normal-project,another-project"):
                    worker["additionalEnv"] = [{"name": "DELETE_SKIP_PROJECT_IDS", "value": exclusions}]
                    result = langfuse(values)
                    deployment = resource(result, "Deployment", "monitor-langfuse-worker")
                    container = deployment["spec"]["template"]["spec"]["containers"][0]
                    self.assertIn(target["additionalEnvFrom"][0], container["envFrom"])
                    self.assertEqual([item for item in container["env"] if item["name"] == "DELETE_SKIP_PROJECT_IDS"],
                                     [{"name": "DELETE_SKIP_PROJECT_IDS", "value": exclusions}])

                for entry in ({"value": "normal-project,rejected-request-debug"},
                              {"valueFrom": {"configMapKeyRef": {"name": "imported-worker-settings", "key": "skip"}}},
                              {"value": None}):
                    worker["additionalEnv"] = [{"name": "DELETE_SKIP_PROJECT_IDS", **entry}]
                    result = render("langfuse", values, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("rejectedRequestDebug requires", result.stderr)

    def test_disabled_has_no_remote_capture_authority(self):
        result = extproc({})
        pod = resource(result, "Deployment")["spec"]["template"]["spec"]
        self.assertFalse(any(item["name"].startswith(PREFIX) for item in pod["containers"][0]["env"]))
        self.assertFalse(any(doc["kind"] in ("ServiceAccount", "SecretStore", "ExternalSecret")
                             for doc in documents(result)))
        self.assertNotIn(SECRET, result.stdout)
        self.assertFalse(peer_rules(result, "monitor-agentgateway-extproc-network-policy",
                                    "egress", "monitor-langfuse"))
        self.assertFalse(peer_rules(langfuse({}), "monitor-langfuse-web-network-policy",
                                    "ingress", "monitor-agentgateway-extproc"))
        self.assertFalse(any(doc["kind"] == "CronJob" for doc in documents(langfuse({}))))

    def test_enabled_scopes_credentials_and_both_network_peers(self):
        values = {"rejectedRequestDebug": {"enabled": True}}
        result = extproc(values)
        deployment = resource(result, "Deployment")
        pod = deployment["spec"]["template"]["spec"]
        env = {item["name"]: item for item in pod["containers"][0]["env"]}
        for field, key in (("PUBLIC_KEY", "public-key"), ("SECRET_KEY", "secret-key")):
            self.assertEqual(env[PREFIX + field]["valueFrom"], {
                "secretKeyRef": {"name": SECRET, "key": key, "optional": True},
            })
            self.assertNotIn("value", env[PREFIX + field])
        self.assertIn(SECRET, deployment["metadata"]["annotations"]["secret.reloader.stakater.com/reload"])
        self.assertFalse(any(volume.get("emptyDir") is not None for volume in pod["volumes"]))
        self.assertFalse(any(item.startswith("EXTPROC_REJECTION_CAPTURE__") for item in env))
        self.assertFalse(pod["automountServiceAccountToken"])
        store = resource(result, "SecretStore")["spec"]["provider"]["vault"]
        self.assertEqual(store["auth"]["kubernetes"], {
            "mountPath": "kubernetes", "role": IDENTITY,
            "serviceAccountRef": {"name": IDENTITY, "audiences": ["openbao"]},
        })
        account = resource(result, "ServiceAccount")
        self.assertEqual(account["metadata"]["name"], IDENTITY)
        self.assertFalse(account["automountServiceAccountToken"])
        secret = resource(result, "ExternalSecret")["spec"]
        self.assertEqual(secret["secretStoreRef"], {"name": IDENTITY, "kind": "SecretStore"})
        self.assertNotIn("dataFrom", secret)
        self.assertEqual({(row["remoteRef"]["key"], row["remoteRef"]["property"]) for row in secret["data"]}, {
            ("monitor-agentgateway-extproc/langfuse-debug", "publicKey"),
            ("monitor-agentgateway-extproc/langfuse-debug", "secretKey"),
        })
        self.assertEqual(secret["target"]["creationPolicy"], "Owner")
        self.assertEqual(secret["target"]["deletionPolicy"], "Delete")
        for rendered, policy, direction, namespace, labels in (
            (result, "monitor-agentgateway-extproc-network-policy", "egress", "monitor-langfuse",
             {"app.kubernetes.io/name": "langfuse", "app.kubernetes.io/instance": "monitor-langfuse", "app": "web"}),
            (langfuse(values), "monitor-langfuse-web-network-policy", "ingress", "monitor-agentgateway-extproc",
             {"app.kubernetes.io/name": "monitor-agentgateway-extproc",
              "app.kubernetes.io/instance": "monitor-agentgateway-extproc"}),
        ):
            rules = peer_rules(rendered, policy, direction, namespace)
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0]["ports"], [{"port": 3000, "protocol": "TCP"}])
            peers = rules[0]["to" if direction == "egress" else "from"]
            self.assertEqual(len(peers), 1)
            self.assertEqual(peers[0]["podSelector"]["matchLabels"], labels)

    def test_rejects_ambiguous_gate_unbounded_delivery_and_wrong_destination(self):
        for chart in ("agentgateway-extproc", "langfuse"):
            for enabled in ("false", 1):
                with self.subTest(chart=chart, enabled=enabled):
                    result = render(chart, {"rejectedRequestDebug": {"enabled": enabled}}, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("rejectedRequestDebug.enabled must be boolean", result.stderr)
        for field, value in (("maxCaptureBytes", 1048577), ("maxCaptureBytes", "65536"),
                             ("queueCapacity", 0), ("queueCapacity", 129),
                             ("timeoutSeconds", True), ("timeoutSeconds", 0),
                             ("timeoutSeconds", 11), ("endpoint", "https://example.com/v1/traces")):
            with self.subTest(field=field, value=value):
                result = render("agentgateway-extproc", {
                    "rejectedRequestDebug": {"enabled": True},
                    "monitorAgentgatewayExtproc": {"rejectionLangfuse": {field: value}},
                }, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("monitorAgentgatewayExtproc.rejectionLangfuse", result.stderr)
