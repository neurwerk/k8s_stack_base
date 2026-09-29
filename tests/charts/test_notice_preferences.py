"""Guard the mandatory private notice preference wire contract."""

import unittest
from pathlib import Path

import yaml

from helm import documents, render, resource


class NoticePreferenceBoundaryTests(unittest.TestCase):
    def test_credential_metadata_uses_verified_key_identity_only(self):
        models = {"guardrails": {"llmPolicyEngine": {"models": [{
            "name": "remote/example/model", "provider": "OpenAI",
            "model": "example-model", "baseURL": "https://provider.example.test",
            "piiEnabled": True, "contentTracingEnabled": True,
        }]}}}
        rendered = render("agentgateway", models)
        auth = resource(rendered, "AgentgatewayPolicy", "infra-agentgateway-auth-ag-policy")
        policy = resource(rendered, "AgentgatewayPolicy", "infra-agentgateway-policy-extproc")
        conditions = policy["spec"]["traffic"]["extProc"]["conditional"]
        bridge = auth["spec"]["traffic"]["extAuth"]["conditional"][0]["policy"]["http"]["responseMetadata"]
        self.assertIn("credential_id", bridge)
        self.assertIn("credential_kind", bridge)
        self.assertEqual(len(conditions), 3)
        key_metadata = conditions[0]["policy"]["metadataContext"]["neurwerk.destination_policy"]
        self.assertIn("extauthz.credential_id", conditions[0]["condition"])
        self.assertEqual(key_metadata["credential_id"], "extauthz.credential_id")
        jwt_metadata = conditions[1]["policy"]["metadataContext"]["neurwerk.destination_policy"]
        self.assertNotIn("credential_id", jwt_metadata)
        legacy = conditions[2]["policy"]["metadataContext"]["neurwerk.destination_policy"]
        self.assertNotIn("credential_id", legacy)
        self.assertIn("!has(jwt.sub)", conditions[2]["condition"])

    def test_private_listener_schema_and_lookup_wire(self):
        studio = render("studio/api", namespace="frontend-studio", release="frontend-studio-api")
        deployment = resource(studio, "Deployment", "frontend-studio-api-deployment")
        pod = deployment["spec"]["template"]["spec"]
        self.assertEqual(pod["initContainers"][0]["command"], ["k8s-stack-studio-migrate"])
        private = next(c for c in pod["containers"] if c["name"] == "notice-preferences")
        self.assertEqual(private["command"], ["k8s-stack-studio-notices"])
        env = {item["name"]: item for item in private["env"]}
        self.assertEqual(env["K8S_STUDIO_NOTICE_CLIENT_CN"]["value"], "monitor-agentgateway-extproc-studio")
        self.assertEqual(env["K8S_STUDIO_NOTICE_POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"], "password")
        service = resource(studio, "Service", "frontend-studio-notice-preferences")
        self.assertEqual(service["spec"]["ports"][0]["targetPort"], "preferences")
        extproc = render("agentgateway-extproc", namespace="monitor-agentgateway-extproc", release="monitor-agentgateway-extproc")
        workload = next(d for d in documents(extproc) if d["kind"] == "Deployment")
        variables = {e["name"]: e["value"] for e in workload["spec"]["template"]["spec"]["containers"][0]["env"] if "value" in e}
        self.assertEqual(variables["EXTPROC_NOTICE_PREFERENCES__ENABLED"], "true")
        self.assertEqual(variables["EXTPROC_NOTICE_PREFERENCES__BASE_URL"], "https://frontend-studio-notice-preferences.frontend-studio.svc.cluster.local:443")
        self.assertEqual(variables["EXTPROC_NOTICE_PREFERENCES__CLIENT_CERT"], "/var/run/studio/tls/tls.crt")
        server_cert = resource(studio, "Certificate", "frontend-studio-notice-preferences-certificate")
        client_cert = resource(extproc, "Certificate", "monitor-agentgateway-extproc-studio-client-certificate")
        self.assertEqual(server_cert["spec"]["usages"], ["server auth"])
        self.assertEqual(client_cert["spec"]["usages"], ["client auth"])
        policies = render("cert-manager/approval-policy")
        for certificate, policy_name in ((server_cert, "cert-manager-internal-studio-notice-preferences-server"),
                                         (client_cert, "cert-manager-internal-agentgateway-extproc-studio-client")):
            policy = resource(policies, "CertificateRequestPolicy", policy_name)
            self.assertEqual(policy["spec"]["allowed"]["commonName"]["value"], certificate["spec"]["commonName"])
            self.assertEqual(policy["spec"]["allowed"]["usages"], certificate["spec"]["usages"])
            self.assertIn(certificate["metadata"]["namespace"], policy["spec"]["selector"]["namespace"]["matchNames"])

    def test_notice_components_cannot_be_disabled(self):
        for chart, value in (("studio/api", {"frontendStudio": {"api": {"postgres": {"enabled": False}}}}),
                             ("studio/api", {"frontendStudio": {"api": {"noticePreferences": {"enabled": False}}}}),
                             ("agentgateway-extproc", {"monitorAgentgatewayExtproc": {"noticePreferences": {"enabled": False}}}),
                             ("agentgateway", {"guardrails": {"llmPolicyEngine": {"credentialContextEnabled": False}}}),
                             ("postgres/operations", {"studio": {"enabled": False}})):
            with self.subTest(chart=chart, value=value):
                rendered = render(chart, value, check=False)
                self.assertNotEqual(rendered.returncode, 0)
                self.assertIn("required for this Base release", rendered.stderr)

    def test_database_provisioning_and_required_secret_contract(self):
        postgres = render("postgres/operations",
                          namespace="infra-postgres-operations", release="postgres-operations")
        job = resource(postgres, "Job", "postgres-operations-provision")
        script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
        self.assertIn("Existing Studio database lacks the provisioner marker; refusing takeover", script)
        self.assertIn("Existing Studio role/database pair is incomplete", script)
        self.assertIn("CREATE DATABASE studio OWNER studio", script)
        root = Path(__file__).resolve().parents[2]
        sync = list(yaml.safe_load_all((root / "releases/studio/secret-sync/postgres.yaml").read_text()))
        self.assertEqual(sync[1]["spec"]["data"][0]["remoteRef"]["property"], "postgresqlPassword")
        manifest = yaml.safe_load((root / "release/manifest.yaml").read_text())
        required = manifest["spec"]["packages"]["default"]
        for path in ("releases/studio/secret-sync", "releases/keycloak-api-key-bridge/secret-sync"):
            self.assertLess(required.index(path), required.index("releases/infrastructure"))
            self.assertNotIn(path, [item["path"] for item in manifest["spec"]["packages"]["optional"]])

if __name__ == "__main__":
    unittest.main()
