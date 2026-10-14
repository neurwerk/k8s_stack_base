"""Guard public-only gateway mounts and trust publication readiness."""

import unittest

from helm import render, resource


class StudioGatewayTests(unittest.TestCase):
    def test_public_projection_and_exact_replica_readiness(self):
        digest = "a" * 64
        values = {"frontendStudio": {"api": {"gateway": {
            "enabled": True,
            "statusConfigMapName": "agentgateway-status-config-map",
            "trust": {"enabled": True, "requiredVersion": digest},
        }}}}
        rendered = render("studio/api", values, namespace="frontend-studio")
        pod = resource(rendered, "Deployment", "frontend-studio-api-deployment")["spec"]["template"]["spec"]
        api = next(c for c in pod["containers"] if c["name"].endswith("-api"))
        env = {e["name"]: e.get("value") for e in api["env"]}
        self.assertEqual(env["STUDIO_GATEWAY_TRUST_REQUIRED_VERSION"], digest)
        projected = {v["name"]: v for v in pod["volumes"] if v["name"].startswith("gateway-")}
        self.assertEqual(set(projected), {"gateway-catalog", "gateway-status", "gateway-trust"})
        self.assertTrue(all("configMap" in v and "secret" not in v for v in projected.values()))
        for mount in api["volumeMounts"]:
            if mount["name"] in projected:
                self.assertTrue(mount["readOnly"])
                self.assertNotIn("subPath", mount)
        for container in pod["containers"]:
            if container is not api:
                self.assertFalse(any(m["name"] in projected for m in container.get("volumeMounts", [])))
        self.assertEqual(api["readinessProbe"]["httpGet"]["path"], f"/api/gateway/trust/bundles/{digest}.pem")
        for probe in ("startupProbe", "livenessProbe"):
            self.assertEqual(api[probe]["httpGet"]["path"], "/api/version")
        ingress = resource(rendered, "NetworkPolicy", "frontend-studio-notice-preferences-ingress")["spec"]["ingress"]
        publisher = [r for r in ingress if any(
            p.get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name")
            == "agentgateway-inspection-trust" for p in r["from"]
        )]
        self.assertEqual(len(publisher), 1)
        self.assertEqual(publisher[0]["ports"], [{"port": 4010, "protocol": "TCP"}])
        self.assertEqual(publisher[0]["from"][0]["namespaceSelector"]["matchLabels"], {
            "kubernetes.io/metadata.name": "infra-agentgateway",
        })

    def test_disabled_gateway_has_no_trust_consumer_or_publisher_access(self):
        rendered = render("studio/api", namespace="frontend-studio")
        pod = resource(rendered, "Deployment", "frontend-studio-api-deployment")["spec"]["template"]["spec"]
        self.assertFalse(any(v["name"].startswith("gateway-") for v in pod["volumes"]))
        self.assertNotIn("STUDIO_GATEWAY_", rendered.stdout)
        self.assertNotIn("agentgateway-inspection-trust", rendered.stdout)

    def test_invalid_trust_rollout_inputs_fail_before_reconciliation(self):
        for gateway in (
            {"enabled": "true"},
            {"trust": {"enabled": True}},
            {"trust": {"requiredVersion": "a" * 64}},
            {"enabled": True, "trust": {"enabled": True, "requiredVersion": "../bundle"}},
            {"enabled": True, "trust": {"enabled": True, "requiredVersion": "A" * 64}},
            {"enabled": True, "catalogConfigMapName": ""},
            {"enabled": True, "catalogConfigMapName": "invalid..name"},
            {"enabled": True, "statusConfigMapName": True},
            {"enabled": True, "trust": {"enabled": True, "requiredVersion": 123}},
        ):
            with self.subTest(gateway=gateway):
                result = render("studio/api", {"frontendStudio": {"api": {"gateway": gateway}}}, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Studio gateway", result.stderr)


if __name__ == "__main__":
    unittest.main()
