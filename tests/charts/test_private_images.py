"""Version-gated image metadata, local face routes and Docling aliases (offline)."""

import json
import re
import unittest

from catalog import agent_values, catalog
from helm import env_value, render, resource, resources


class PrivateImageTests(unittest.TestCase):
    def values(self, version=2, **row):
        model = {"name": "direct", "provider": "Custom", "model": "vision", **row}
        values = agent_values(catalog(), [model])
        values["authKeycloak"]["agentgatewayClientRoles"].append("model:direct:invoke")
        values["guardrails"]["llmPolicyEngine"]["attachmentPolicyVersion"] = version
        return values

    def metadata(self, values):
        result = render("agentgateway", values)
        return {
            key: json.loads(json.loads(value))
            for key, value in re.findall(
                r'(?m)^                (contract_version|models|attachment_modes|document_modes|image_modes|image_forwarding|face_protection|local_models|image_models|image_reroutes): (".*")$',
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
        for version in (1, 2):
            engine["attachmentPolicyVersion"] = version
            for source in (engine["models"][0], values["openrouterCatalog"]["models"][0],
                           engine["localTarget"]):
                for capability in (True, False):
                    source["supportsImages"] = capability
                    result = render("agentgateway", values, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("requires attachmentPolicyVersion: 3", result.stderr)
                del source["supportsImages"]

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
        for version in ("2", "3", True, False, 0, 4, 1.5, None, [], {}):
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

    def test_v3_capability_and_standard_reader_gate(self):
        for mode in ("cpu", "internal-standard", "remote", "private-vlm"):
            values = self.values(version=3, attachmentMode="process", local=True,
                                 supportsImages=True, imageForwarding="pii-unchecked",
                                 faceProtectionEnabled=False)
            values["docling"] = {"enabled": True, "inference": {"mode": mode}}
            metadata = self.metadata(values)
            self.assertEqual(metadata["contract_version"], 3)
            self.assertEqual(metadata["image_models"], {"direct": True, "remote/openrouter/acme/model": False})
            self.assertEqual(metadata["image_reroutes"], {})
        engine = values["guardrails"]["llmPolicyEngine"]
        row = engine["models"][0]
        for capability in (None, False, "true", 1):
            row.pop("supportsImages", None)
            if capability is not None:
                row["supportsImages"] = capability
            result = render("agentgateway", values, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("supportsImages", result.stderr)
        row.update(imageForwarding="if-no-pii-detected", faceProtectionEnabled=True)
        for source in (row, engine["localTarget"], values["openrouterCatalog"]["models"][0]):
            row.pop("supportsImages", None)
            source["supportsImages"] = "false"
            self.assertIn("supportsImages must be a boolean", render("agentgateway", values, check=False).stderr)
            del source["supportsImages"]
        values["openrouterCatalog"]["models"][0]["supportsImages"] = True
        row.update(local=False, supportsImages=True, baseURL="http://127.0.0.1:11434")
        self.assertFalse(any(self.metadata(values)["image_models"].values()))
        row["local"] = True
        row["piiReroute"] = True
        self.assertFalse(self.metadata(values)["image_models"]["direct"])
        del row["piiReroute"]
        values["docling"] = {"enabled": True, "inference": {"mode": "internal-standard"}}
        self.assertTrue(self.metadata(values)["image_models"]["direct"])
        values["docling"]["enabled"] = False
        self.assertIn("requires enabled Docling", render("agentgateway", values, check=False).stderr)

    def test_v3_face_routes_follow_actual_ordered_local_targets(self):
        values = self.values(version=3, local=True, supportsImages=True,
                             attachmentMode="process", imageForwarding="if-no-pii-detected")
        engine = values["guardrails"]["llmPolicyEngine"]
        source = values["openrouterCatalog"]["models"][0]
        source.update(attachmentMode="process", imageForwarding="if-no-pii-detected")
        policy = values["monitorPiiEngine"]["policy"]
        policy["attachments"] = {"faces": {"action": "reroute"}}
        policy["routing"].update(defaultTarget=source["name"], targets=[
            {"name": source["name"], "classPrefix": "remote/"},
            {"name": "direct", "classPrefix": "remote/openrouter/"},
        ])
        expected = {name: {source["name"]: "direct"} for name in ("direct", source["name"])}
        self.assertEqual(self.metadata(values)["image_reroutes"], expected)
        policy["attachments"]["faces"]["routeClass"] = "x" * 128
        self.assertIn("x" * 128, self.metadata(values)["image_reroutes"]["direct"])
        for settings, field in ((policy["attachments"]["faces"], "routeClass"),
                                (policy["routing"], "defaultTarget")):
            settings[field] = "x" * 129
            result = render("agentgateway", values, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("1-128 character routeClass or routing.defaultTarget", result.stderr)
            settings[field] = source["name"]
            policy["attachments"]["faces"].pop("routeClass", None)
        # Named routes retain their own permission; the source is not rewritten.
        rendered = render("agentgateway", values)
        named = resource(rendered, "AgentgatewayModel", "direct")["spec"]
        self.assertIn("model:direct:invoke", str(named["policies"]["authorization"]))
        virtual = resource(rendered, "AgentgatewayModel", "remote-openrouter-acme-model")["spec"]
        self.assertIn('x-remote-allowed', virtual["virtualModel"]["conditional"]["targets"][0]["when"])
        self.assertEqual(virtual["match"]["model"], source["name"])
        # First matching local target blocks approval if incapable or ambiguous,
        # even when a later target or the dedicated fallback supports images.
        engine["localTarget"]["supportsImages"] = True
        engine["models"].append({"name": "later", "local": True, "model": "vision", "supportsImages": True})
        values["authKeycloak"]["agentgatewayClientRoles"].append("model:later:invoke")
        policy["routing"]["targets"].append({"name": "later", "classPrefix": "remote/openrouter/acme/"})
        for capability in (False, None):
            engine["models"][0].pop("supportsImages", None)
            if capability is not None:
                engine["models"][0]["supportsImages"] = capability
            self.assertEqual(self.metadata(values)["image_reroutes"], {})
        engine["models"][0].update(supportsImages=True, piiReroute=True)
        self.assertEqual(self.metadata(values)["image_reroutes"], {})
        del engine["models"][0]["piiReroute"]
        policy["attachments"]["faces"]["routeClass"] = "direct"
        policy["routing"]["defaultTarget"] = "direct"
        policy["routing"]["targets"] = [{"name": "direct"}]
        self.assertEqual(self.metadata(values)["image_reroutes"][source["name"]], {"direct": "direct"})
        policy["attachments"]["faces"]["routeClass"] = "unmatched"
        self.assertEqual(self.metadata(values)["image_reroutes"][source["name"]], {
            "unmatched": "remote-openrouter-acme-model-local",
        })
        fallback = resource(render("agentgateway", values), "AgentgatewayModel", "remote-openrouter-acme-model-local")["spec"]
        self.assertIn(f'model:{source["name"]}:invoke', str(fallback["policies"]["authorization"]))
        del engine["localTarget"]["supportsImages"]
        self.assertNotIn(source["name"], self.metadata(values)["image_reroutes"])
        engine["models"][0].update(local=False, baseURL="http://127.0.0.1:11434")
        self.assertEqual(self.metadata(values)["image_reroutes"], {})
        engine["models"][0]["local"] = True
        for mode, forwarding in (("process", "none"), ("block", "none"), ("extract", "none")):
            engine["models"][0].update(attachmentMode=mode, imageForwarding=forwarding)
            self.assertEqual(self.metadata(values)["image_reroutes"], {})
        engine["models"][0].update(attachmentMode="extract", imageForwarding="if-no-pii-detected")
        for action in ("block", "text-only"):
            policy["attachments"]["faces"] = {"action": action}
            self.assertEqual(self.metadata(values)["image_reroutes"], {})
            policy["attachments"]["faces"]["routeClass"] = "direct"
            self.assertIn("only allowed with action reroute", render("agentgateway", values, check=False).stderr)

    def test_policy_aware_images_require_v3_and_complete_protections(self):
        for version, override, error in (
            (2, {}, "requires attachmentPolicyVersion: 3"),
            (3, {"piiEnabled": False}, "requires PII and face protection"),
            (3, {"faceProtectionEnabled": False}, "requires PII and face protection"),
            (3, {"attachmentMode": "block"}, "requires attachmentMode process or extract"),
        ):
            values = self.values(version=version, **{
                "attachmentMode": "process", "imageForwarding": "if-policy-allows", **override,
            })
            result = render("agentgateway", values, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(error, result.stderr)
        values = self.values(version=3, attachmentMode="process", imageForwarding="if-policy-allows")
        values["docling"] = {"enabled": True, "inference": {"mode": "internal-standard"}}
        source = values["openrouterCatalog"]["models"][0]
        source.update(attachmentMode="extract", imageForwarding="if-policy-allows")
        self.assertEqual(self.metadata(values)["image_forwarding"], {
            "direct": "if-policy-allows", source["name"]: "if-policy-allows",
        })
        values["docling"]["enabled"] = False
        self.assertIn("requires enabled Docling", render("agentgateway", values, check=False).stderr)

    def test_v4_defaults_and_versioned_fields(self):
        values = self.values(version=4, attachments={})
        values["openrouterCatalog"]["models"][0]["attachments"] = {}
        metadata = self.metadata(values)
        names = set(metadata["models"])
        self.assertEqual(metadata["contract_version"], 4)
        self.assertNotIn("attachment_modes", metadata)
        self.assertEqual(metadata["document_modes"], {name: "block" for name in names})
        self.assertEqual(metadata["image_modes"], {name: "block" for name in names})
        self.assertEqual(metadata["image_forwarding"], {name: "none" for name in names})
        self.assertEqual(metadata["face_protection"], {name: False for name in names})

        for field, value in (("attachmentMode", "block"), ("imageForwarding", "none"),
                             ("faceProtectionEnabled", False)):
            values["guardrails"]["llmPolicyEngine"]["models"][0][field] = value
            failed = render("agentgateway", values, check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("is not allowed with attachmentPolicyVersion: 4", failed.stderr)
            del values["guardrails"]["llmPolicyEngine"]["models"][0][field]

        for version in (1, 2, 3):
            values["guardrails"]["llmPolicyEngine"]["attachmentPolicyVersion"] = version
            failed = render("agentgateway", values, check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("nested attachments requires attachmentPolicyVersion: 4", failed.stderr)

        values["guardrails"]["llmPolicyEngine"]["attachmentPolicyVersion"] = 4
        del values["guardrails"]["llmPolicyEngine"]["models"][0]["attachments"]
        self.assertIn("requires nested attachments", render("agentgateway", values, check=False).stderr)

    def test_v4_derives_typed_policy_and_validates_combinations(self):
        cases = (
            ({"documents": {"mode": "extract-text"}}, True, "none", True),
            ({"images": {"mode": "extract-text"}}, False, "none", True),
            ({"images": {"mode": "forward-normalized"}}, True, "if-policy-allows", True),
            ({"images": {"mode": "forward-normalized", "policy": "strict"}}, True,
             "if-no-pii-detected", True),
        )
        for attachments, pii, forwarding, face in cases:
            with self.subTest(attachments=attachments, pii=pii):
                values = self.values(version=4, attachments=attachments, piiEnabled=pii,
                                     supportsImages=True)
                values["openrouterCatalog"]["models"] = []
                values["docling"] = {"enabled": True, "inference": {"mode": "internal-standard"}}
                metadata = self.metadata(values)
                self.assertEqual(metadata["document_modes"]["direct"],
                                 attachments.get("documents", {}).get("mode", "block"))
                self.assertEqual(metadata["image_modes"]["direct"],
                                 attachments.get("images", {}).get("mode", "block"))
                self.assertEqual(metadata["image_forwarding"]["direct"], forwarding)
                self.assertEqual(metadata["face_protection"]["direct"], face)

        values = self.values(
            version=4,
            attachments={"images": {"mode": "forward-normalized", "policy": "unchecked"}},
            piiEnabled=False, local=True, supportsImages=True,
        )
        values["openrouterCatalog"]["models"] = []
        values["docling"] = {"enabled": True, "inference": {"mode": "private-vlm"}}
        metadata = self.metadata(values)
        self.assertEqual(metadata["image_forwarding"], {"direct": "pii-unchecked"})
        self.assertEqual(metadata["face_protection"], {"direct": False})
        self.assertEqual(metadata["image_models"], {"direct": True})

        invalid = (
            ({"documents": {"mode": "extract"}}, {}, "documents.mode"),
            ({"images": {"mode": "process"}}, {}, "images.mode"),
            ({"images": {"mode": "block", "policy": "strict"}}, {}, "only allowed"),
            ({"images": {"mode": "forward-normalized"}}, {}, "supportsImages:true"),
            ({"images": {"mode": "forward-normalized"}}, {"supportsImages": True, "piiEnabled": False}, "requires PII"),
            ({"images": {"mode": "forward-normalized", "policy": "unchecked"}},
             {"supportsImages": True, "local": True}, "requires piiEnabled:false"),
            ({"images": {"mode": "forward-normalized", "policy": "unchecked"}},
             {"supportsImages": True, "piiEnabled": False}, "concrete local:true"),
        )
        for attachments, row, error in invalid:
            with self.subTest(attachments=attachments, row=row):
                values = self.values(version=4, attachments=attachments, **row)
                values["openrouterCatalog"]["models"] = []
                values["docling"] = {"enabled": True, "inference": {"mode": "internal-standard"}}
                self.assertIn(error, render("agentgateway", values, check=False).stderr)

        for inference in (None, "public"):
            values = self.values(version=4, attachments={"images": {"mode": "extract-text"}})
            values["openrouterCatalog"]["models"] = []
            values["docling"] = ({"enabled": False} if inference is None else
                                 {"enabled": True, "inference": {"mode": inference}})
            self.assertIn("non-block attachment modes require enabled Docling",
                          render("agentgateway", values, check=False).stderr)

    def test_v4_reroutes_preserve_concrete_destination_proof(self):
        values = self.values(
            version=4, attachments={}, local=True, supportsImages=True,
        )
        source = values["openrouterCatalog"]["models"][0]
        source.update(attachments={"images": {"mode": "forward-normalized"}}, supportsImages=True)
        values["docling"] = {"enabled": True, "inference": {"mode": "internal-standard"}}
        policy = values["monitorPiiEngine"]["policy"]
        policy["attachments"] = {"faces": {"action": "reroute", "routeClass": "faces/local"}}
        policy["routing"]["targets"] = [{"name": "direct", "classPrefix": "faces/"}]
        metadata = self.metadata(values)
        self.assertEqual(metadata["image_reroutes"], {
            source["name"]: {"faces/local": "direct"},
        })
        values["guardrails"]["llmPolicyEngine"]["models"][0].update(local=True, supportsImages=False)
        self.assertEqual(self.metadata(values)["image_reroutes"], {})

    def test_v4_typed_mode_maps_keep_independent_size_limits(self):
        values = self.values(version=4, attachments={})
        values["guardrails"]["llmPolicyEngine"]["models"] = []
        values["openrouterCatalog"]["models"] = [
            {"name": f"remote/{index:03d}/" + "x" * 44, "upstreamModel": f"acme/{index}",
             "attachments": {"documents": {"mode": "extract-text"}}}
            for index in range(256)
        ]
        values["docling"] = {"enabled": True, "inference": {"mode": "internal-standard"}}
        failed = render("agentgateway", values, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("document_modes destination metadata JSON exceeds", failed.stderr)

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
        # The new binding map has its own 16 KiB bound, not the sum of old maps.
        values["guardrails"]["llmPolicyEngine"].update(attachmentPolicyVersion=3)
        values["guardrails"]["llmPolicyEngine"]["localTarget"]["supportsImages"] = True
        values["monitorPiiEngine"]["policy"].update(attachments={"faces": {"action": "reroute", "routeClass": "face"}})
        values["openrouterCatalog"]["models"] = [
            {"name": f"remote/{index:03d}/" + "x" * 25, "upstreamModel": f"acme/{index}",
             "attachmentMode": "process", "imageForwarding": "if-no-pii-detected"}
            for index in range(220)
        ]
        self.assertIn("image_reroutes destination metadata JSON exceeds", render("agentgateway", values, check=False).stderr)

    def test_docling_aliases_are_identical_and_private_scope_remains(self):
        for old, new in (("cpu", "internal-standard"), ("remote", "private-vlm")):
            for chart in ("docling", "agentgateway-extproc"):
                old_result = render(chart, {"docling": {"inference": {"mode": old}}})
                new_result = render(chart, {"docling": {"inference": {"mode": new}}})
                self.assertEqual(old_result.stdout, new_result.stdout)
                if chart == "docling":
                    settings = json.loads(
                        resource(new_result, "ConfigMap")["data"]["settings.json"]
                    )
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
                            self.assertNotIn(
                                text,
                                "\n".join(
                                    resources(new_result, "Deployment")
                                    + resources(new_result, "NetworkPolicy")
                                ),
                            )
                if chart == "agentgateway-extproc":
                    self.assertEqual(env_value(new_result, "EXTPROC_DOCLING__INFERENCE_MODE"), old)
        for inference in (
            {"cidrs": ["0.0.0.0/0"]},
            {"url": "http://inference.test/v1/chat/completions"},
            {"tokenSecretRef": {"name": "docling-api", "key": "api-key"}},
        ):
            result = render(
                "docling",
                {"docling": {"inference": {"mode": "private-vlm", **inference}}},
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
