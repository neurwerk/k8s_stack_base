"""Image inspection service credentials and private network scope."""

import unittest

from helm import env_value, render, resource


class PrivateImageTests(unittest.TestCase):
    def test_v4_1_extproc_image_inspection_wiring(self):
        disabled = render("agentgateway-extproc", {"docling": {"enabled": False}})
        self.assertNotIn("EXTPROC_IMAGE_INSPECTION__", disabled.stdout)
        inspection = {
            "enabled": True,
            "allowHttp": False,
            "baseUrl": "https://inspection.internal:8443",
            "model": "private-vision",
            "tokenSecretRef": {
                "name": "monitor-agentgateway-extproc-image-inspection-secret",
                "key": "api-key",
            },
            "timeout": 120,
            "caConfigMap": "",
            "cidrs": ["10.20.30.40/32"],
            "port": 8443,
        }
        values = {
            "docling": {"enabled": False},
            "monitorAgentgatewayExtproc": {"imageInspection": inspection},
        }
        result = render("agentgateway-extproc", values)
        self.assertEqual(env_value(result, "EXTPROC_IMAGE_INSPECTION__ENABLED"), "true")
        container = resource(result, "Deployment")["spec"]["template"]["spec"]["containers"][0]
        api_key = next(item for item in container["env"] if item["name"] ==
                       "EXTPROC_IMAGE_INSPECTION__API_KEY")
        self.assertEqual(api_key["valueFrom"]["secretKeyRef"], {
            "name": "monitor-agentgateway-extproc-image-inspection-secret", "key": "api-key",
        })
        for override in (
            {"baseUrl": "http://10.20.30.40:8080", "port": 8080},
            {"cidrs": ["8.8.8.8/32"]},
        ):
            failed = render("agentgateway-extproc", {
                "docling": {"enabled": False},
                "monitorAgentgatewayExtproc": {"imageInspection": inspection | override},
            }, check=False)
            self.assertNotEqual(failed.returncode, 0, override)
