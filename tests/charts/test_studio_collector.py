"""Reporting credentials, private access and initialization ordering."""

import unittest

from helm import documents, render, resource

# Synthetic render-only pin, never used in a deployment or release inventory.
IMAGE = "ghcr.io/neurwerk/k8s-stack-studio-collector:0.26.0@sha256:" + "1" * 64


class StudioCollectorTests(unittest.TestCase):
    def test_enabled_job_is_private_and_api_only_gets_read_credentials(self):
        result = render("studio/api", {
            "frontendStudio": {"usage": {"enabled": True, "collectorImage": IMAGE}},
        }, release="frontend-studio-api", namespace="frontend-studio")
        cron = resource(result, "CronJob")["spec"]
        self.assertEqual(cron["schedule"], "*/15 * * * *")
        self.assertEqual(cron["concurrencyPolicy"], "Forbid")
        pod = cron["jobTemplate"]["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["containers"][0]["image"], IMAGE)
        deployment = resource(result, "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(deployment["initContainers"][0]["args"], ["--initialize"])
        env = {v["name"]: v for v in deployment["containers"][0]["env"]}
        self.assertEqual(env["K8S_STUDIO_USAGE_POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"],
                         "reader-password")
        self.assertNotIn("STUDIO_COLLECTOR_POSTGRES_PASSWORD", env)
        policy = resource(result, "NetworkPolicy", "frontend-studio-collector")["spec"]
        self.assertEqual(policy["ingress"], [])
        ports = {p["port"] for rule in policy["egress"] for p in rule["ports"]}
        self.assertEqual(ports, {53, 9712, 15000, 9200})
        search = next(rule for rule in policy["egress"] if rule["ports"] == [
            {"port": 9200, "protocol": "TCP"},
        ])
        self.assertEqual(search["to"], [{
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitor-opensearch"}},
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "opensearch-v3"}},
        }])

    def test_disabled_and_unpinned_configuration(self):
        result = render("studio/api")
        self.assertFalse(any(d["kind"] == "CronJob" for d in documents(result)))
        result = render("studio/api", {"frontendStudio": {"usage": {"enabled": True, "collectorImage": ""}}},
                        check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_roles_and_trusted_metric_dimensions(self):
        result = render("postgres/operations", {"studio": {"usageEnabled": True}})
        job = resource(result, "Job", "postgres-operations-provision")
        script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
        self.assertIn("CREATE DATABASE studio_usage OWNER studio_usage_collector", script)
        self.assertIn("GRANT SELECT ON TABLES TO studio_usage_reader", script)
        self.assertIn("REVOKE ALL ON DATABASE studio_usage FROM PUBLIC", script)
        metrics = resource(render("agentgateway"), "AgentgatewayPolicy", "infra-agentgateway-ux-metrics")
        labels = {v["name"]: v["expression"] for v in metrics["spec"]["frontend"]["metrics"]["attributes"]["add"]}
        self.assertIn("metadata.neurwerk_identity.principal_id", labels["studio_principal_id"])
        self.assertNotIn("studio_request_id", labels)
        self.assertTrue(all("request.headers" not in value for key, value in labels.items()
                            if key.startswith("studio_")))
