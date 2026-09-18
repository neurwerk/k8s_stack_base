"""Version-gated image metadata and private Docling mode aliases (offline)."""

import json
import re
import unittest

from test_openrouter_catalog import agent_values, catalog, env_value, render, resources
import test_docling


class PrivateImageTests(unittest.TestCase):
    def values(self, **row):
        model = {"name": "direct", "provider": "Custom", "model": "vision", **row}
        values = agent_values(catalog(), [model])
        values["authKeycloak"]["agentgatewayClientRoles"].append("model:direct:invoke")
        values["guardrails"]["llmPolicyEngine"]["attachmentPolicyVersion"] = 2
        return values

    def metadata(self, values):
        result = render("agentgateway", values)
        return {
            key: json.loads(json.loads(value))
            for key, value in re.findall(
                r'(?m)^                (contract_version|models|attachment_modes|image_forwarding|face_protection|local_models): (".*")$',
                result.stdout,
            )
        }

    def test_legacy_omission_and_rollout_guard(self):
        values = self.values()
        engine = values["guardrails"]["llmPolicyEngine"]
        del engine["attachmentPolicyVersion"]
        self.assertEqual(self.metadata(values), {
            "contract_version": 1,
            "models": {"direct": True, "remote/openrouter/acme/model": True},
        })
        for field, value in (("attachmentMode", "process"), ("imageForwarding", "none"),
                             ("faceProtectionEnabled", False)):
            for source in (engine["models"][0], values["openrouterCatalog"]["models"][0]):
                source[field] = value
                result = render("agentgateway", values, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("attachmentPolicyVersion: 2", result.stderr)
                del source[field]

    def test_v2_defaults_sparse_modes_and_catalog_replacement(self):
        values = self.values(attachmentMode="process", local=True, contentTracingEnabled=False)
        metadata = self.metadata(values)
        self.assertEqual(metadata["contract_version"], 2)
        self.assertEqual(metadata["attachment_modes"], {"direct": "process"})
        self.assertEqual(metadata["face_protection"], {"direct": True, "remote/openrouter/acme/model": False})
        self.assertEqual(metadata["image_forwarding"], {name: "none" for name in metadata["models"]})
        self.assertEqual(metadata["local_models"], {"direct": True, "remote/openrouter/acme/model": False})
        row = values["openrouterCatalog"]["models"][0]
        row.update(attachmentMode="extract", imageForwarding="if-no-pii-detected", faceProtectionEnabled=True)
        self.assertEqual(self.metadata(values)["image_forwarding"][row["name"]], "if-no-pii-detected")
        replacement = values["guardrails"]["llmPolicyEngine"]["models"][0]
        replacement["name"] = row["name"]
        self.assertEqual(self.metadata(values)["image_forwarding"], {row["name"]: "none"})

    def test_valid_processing_combinations_and_passthrough(self):
        for mode in ("process", "extract"):
            for private_mode in ("remote", "private-vlm"):
                values = self.values(attachmentMode=mode, imageForwarding="if-no-pii-detected")
                values["docling"] = {"enabled": True, "inference": {"mode": private_mode}}
                self.assertEqual(self.metadata(values)["face_protection"]["direct"], True)
            for pii in (True, False):
                values = self.values(attachmentMode=mode, imageForwarding="pii-unchecked",
                                     local=True, faceProtectionEnabled=False, piiEnabled=pii)
                metadata = self.metadata(values)
                self.assertEqual(metadata["models"]["direct"], pii)
                self.assertTrue(metadata["local_models"]["direct"])
            for cpu_mode in ("cpu", "internal-standard"):
                values = self.values(attachmentMode=mode, imageForwarding="none")
                values["docling"] = {"enabled": True, "inference": {"mode": cpu_mode}}
                self.assertEqual(self.metadata(values)["image_forwarding"]["direct"], "none")
        metadata = self.metadata(self.values(attachmentMode="passthrough", piiEnabled=False))
        self.assertFalse(metadata["face_protection"]["direct"])
        self.assertEqual(metadata["image_forwarding"], {"remote/openrouter/acme/model": "none"})
        self.assertFalse(metadata["models"]["direct"])
        self.assertEqual(metadata["attachment_modes"]["direct"], "passthrough")
        self.metadata(self.values(attachmentMode="block", faceProtectionEnabled=True))

    def test_invalid_combinations_and_false_locality(self):
        cases = [
            {"attachmentMode": "passthrough", "piiEnabled": False, "imageForwarding": "none"},
            {"attachmentMode": "passthrough", "piiEnabled": False, "faceProtectionEnabled": True},
            {"attachmentMode": "passthrough", "piiEnabled": True},
            {"attachmentMode": "block", "imageForwarding": "if-no-pii-detected"},
            {"attachmentMode": "process", "imageForwarding": "if-no-pii-detected", "piiEnabled": False},
            {"attachmentMode": "process", "imageForwarding": "if-no-pii-detected", "faceProtectionEnabled": False},
        ]
        for extras in ({}, {"local": False}, {"local": True, "piiReroute": True},
                       {"baseURL": "http://127.0.0.1:11434", "group": "Local", "route_class": "local"},
                       {"local": True, "faceProtectionEnabled": True}):
            cases.append({"attachmentMode": "process", "imageForwarding": "pii-unchecked",
                          "faceProtectionEnabled": False, **extras})
        for row in cases:
            with self.subTest(row=row):
                self.assertNotEqual(render("agentgateway", self.values(**row), check=False).returncode, 0)
        for forwarding in ("if-no-pii-detected", "pii-unchecked"):
            for mode in ("process", "extract"):
                for docling in ({"enabled": False}, *(
                    {"enabled": True, "inference": {"mode": inference}}
                    for inference in ("cpu", "internal-standard", "public")
                )):
                    values = self.values(attachmentMode=mode, imageForwarding=forwarding,
                                         local=True, faceProtectionEnabled=forwarding != "pii-unchecked")
                    values["docling"] = docling
                    result = render("agentgateway", values, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("requires enabled Docling private-vlm or remote", result.stderr)
        values = self.values(attachmentMode="process", imageForwarding="pii-unchecked",
                             local=True, faceProtectionEnabled=False)
        values["infraAgentgatewayWrapper"]["llamacpp"]["host"] = ""
        self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)

    def test_strict_types(self):
        for version in ("2", True, False, 0, 3, 1.5, None, [], {}):
            values = self.values()
            values["guardrails"]["llmPolicyEngine"]["attachmentPolicyVersion"] = version
            self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)
        for field, invalid in {
            "faceProtectionEnabled": ("false", 0, [], {}),
            "imageForwarding": (False, 0, "", "always", [], {}),
            "local": ("true", 1), "piiReroute": ("false", 0),
            "attachmentMode": (False, 0, "", "unknown"),
        }.items():
            for value in invalid:
                with self.subTest(field=field, value=value):
                    self.assertNotEqual(render("agentgateway", self.values(**{field: value}), check=False).returncode, 0)

    def test_metadata_bounds_and_known_ids(self):
        values = self.values()
        for count in (256, 257):
            rows = [{"name": f"remote/{index}", "upstreamModel": f"acme/{index}"} for index in range(count)]
            values["openrouterCatalog"]["models"] = rows
            values["guardrails"]["llmPolicyEngine"]["models"] = []
            result = render("agentgateway", values, check=False)
            if count == 257:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("at most 256", result.stderr)
            else:
                metadata = self.metadata(values)
                self.assertNotIn("attachment_modes", metadata)
                for field in ("image_forwarding", "face_protection", "local_models"):
                    self.assertEqual(set(metadata[field]), set(metadata["models"]))
        # Long IDs fit the PII and attachment maps but overflow image_forwarding.
        values["openrouterCatalog"]["models"] = [
            {"name": f"remote/{index:03d}/" + "x" * 44, "upstreamModel": f"acme/{index}",
             "attachmentMode": "process", "imageForwarding": "if-no-pii-detected"}
            for index in range(220)
        ]
        result = render("agentgateway", values, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("image_forwarding destination metadata JSON exceeds", result.stderr)

    def test_docling_aliases_are_identical_and_private_scope_remains(self):
        for old, new in (("cpu", "internal-standard"), ("remote", "private-vlm")):
            for chart in ("docling", "agentgateway-extproc"):
                old_result = render(chart, {"docling": {"inference": {"mode": old}}})
                new_result = render(chart, {"docling": {"inference": {"mode": new}}})
                self.assertEqual(old_result.stdout, new_result.stdout)
                if chart == "docling":
                    settings = test_docling.DoclingTests.settings(new_result)
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
                                resources(new_result, "Deployment") + resources(new_result, "NetworkPolicy")))
                if chart == "agentgateway-extproc":
                    self.assertEqual(env_value(new_result, "EXTPROC_DOCLING__INFERENCE_MODE"), old)
        for inference in ({"cidrs": ["0.0.0.0/0"]}, {"url": "http://inference.test/v1/chat/completions"},
                          {"tokenSecretRef": {"name": "docling-api", "key": "api-key"}}):
            result = render("docling", {"docling": {"inference": {"mode": "private-vlm", **inference}}}, check=False)
            self.assertNotEqual(result.returncode, 0)
