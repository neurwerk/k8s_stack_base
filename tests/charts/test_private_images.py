"""Image inspection service credentials and private network scope."""

import json
import unittest

from helm import env_value, render, resource, resources


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

    def test_docling_aliases_are_identical_and_private_scope_remains(self):
        for old, new in (("cpu", "internal-standard"), ("remote", "private-vlm")):
            for chart in ("docling", "agentgateway-extproc"):
                old_result = render(chart, {"docling": {"inference": {"mode": old}}})
                new_result = render(chart, {"docling": {"inference": {"mode": new}}})
                self.assertEqual(old_result.stdout, new_result.stdout)
                if chart == "docling":
                    settings = json.loads(resource(new_result, "ConfigMap")["data"]["settings.json"])
                    if old == "remote":
                        self.assertEqual(settings["allowed_vlm_presets"], ["images"])
                        self.assertEqual(set(settings["custom_vlm_presets"]), {"default", "images"})
                        self.assertEqual(settings["custom_vlm_presets"]["images"]["scale"], 1)
                        self.assertIsNone(settings["custom_vlm_presets"]["images"]["max_size"])
                        self.assertEqual(settings["custom_vlm_presets"]["default"]["scale"], 2)
                    else:
                        self.assertEqual(settings["custom_vlm_presets"], {})
                        self.assertEqual(settings["allowed_vlm_presets"], [])
                        self.assertFalse(settings["enable_remote_services"])
                        for text in ("DOCLING_INFERENCE_TOKEN", "REQUESTS_CA_BUNDLE", "ipBlock:"):
                            self.assertNotIn(text, "\n".join(
                                resources(new_result, "Deployment") + resources(new_result, "NetworkPolicy")
                            ))
                if chart == "agentgateway-extproc":
                    self.assertEqual(env_value(new_result, "EXTPROC_DOCLING__INFERENCE_MODE"), old)
        for inference in (
            {"cidrs": ["0.0.0.0/0"]},
            {"url": "http://inference.test/v1/chat/completions"},
            {"tokenSecretRef": {"name": "docling-api", "key": "api-key"}},
        ):
            result = render("docling", {
                "docling": {"inference": {"mode": "private-vlm", **inference}},
            }, check=False)
            self.assertNotEqual(result.returncode, 0)
