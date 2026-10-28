"""Companion metadata identity, source validation and routing isolation."""

import copy
import hashlib
import json
import unittest

from helm import documents, render, resource
from test_agentgateway_grouped import capacity_values
from test_agentgateway_inspection import binding_values


CAPABILITIES = (
    "streaming", "tool_calling", "streaming_tool_calling", "structured_output",
    "image_input", "audio_input", "video_input", "document_input",
)


def record(binding="openai", model="example-chat"):
    return {
        "binding_id": binding, "public_id": model,
        "limits": {"context_tokens": 8192, "max_input_tokens": 8192, "max_output_tokens": 8192},
        "capabilities": {name: "unknown" for name in CAPABILITIES},
        "provenance": {
            "kind": "reviewed_config", "source": "synthetic reviewed configuration",
            "observed_at": "2020-01-01T00:00:00Z", "valid_until": "2020-01-02T00:00:00Z",
        },
    }


def values_with_metadata():
    values = binding_values()
    values["gatewayModelMetadata"] = {"enabled": True, "models": [record()]}
    return values


def metadata(result):
    config = resource(result, "ConfigMap", "gateway-model-metadata-config-map")
    assert config["metadata"]["namespace"] == "frontend-studio"
    return json.loads(config["data"]["metadata.json"])


class GatewayMetadataTests(unittest.TestCase):
    def test_metadata_only_changes_leave_primary_and_runtime_resources_identical(self):
        values = values_with_metadata()
        # Go encoding/json uses UTF-8, HTML escaping and escaped line separators.
        # Exercise the exact producer/consumer contract, not Python's ASCII default.
        values["gatewayModelMetadata"]["models"][0]["provenance"]["source"] = "reviewed & <café>\u2028line\u2029paragraph"
        first = render("agentgateway", values)
        values["gatewayModelMetadata"]["models"][0]["limits"]["max_output_tokens"] = 100
        second = render("agentgateway", values)
        values["gatewayModelMetadata"]["enabled"] = False
        disabled = render("agentgateway", values)

        def without_companion(result):
            return [doc for doc in documents(result)
                    if doc["metadata"]["name"] != "gateway-model-metadata-config-map"]

        self.assertEqual(without_companion(first), without_companion(second))
        self.assertEqual(without_companion(first), documents(disabled))
        primary = resource(first, "ConfigMap", "agentgateway-catalog-config-map")["data"]["catalog.json"]
        self.assertEqual(primary, resource(second, "ConfigMap", "agentgateway-catalog-config-map")["data"]["catalog.json"])
        companion = metadata(first)
        self.assertEqual(companion["catalog_revision"], json.loads(primary)["catalog_revision"])
        self.assertNotEqual(companion["metadata_revision"], metadata(second)["metadata_revision"])
        revision = companion.pop("metadata_revision")
        canonical = json.dumps(companion, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        for raw, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                             ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
            canonical = canonical.replace(raw, escaped)
        self.assertEqual(revision, hashlib.sha256(canonical.encode()).hexdigest())

    def test_limits_unknown_facts_and_stale_structural_timestamps(self):
        values = values_with_metadata()
        # Freshness belongs to readers, not render-time wall-clock checks.
        result = metadata(render("agentgateway", values))["models"][0]
        self.assertEqual(result["limits"]["max_input_tokens"], 8192)
        self.assertEqual(result["capabilities"], record()["capabilities"])
        values["gatewayModelMetadata"]["models"][0]["limits"] = dict.fromkeys(record()["limits"])
        self.assertEqual(metadata(render("agentgateway", values))["models"][0]["limits"], dict.fromkeys(record()["limits"]))
        for field in record()["limits"]:
            for bad in (True, "100", 1.5, 0, -1):
                candidate = values_with_metadata()
                candidate["gatewayModelMetadata"]["models"][0]["limits"][field] = bad
                with self.subTest(field=field, bad=bad):
                    self.assertNotEqual(render("agentgateway", candidate, check=False).returncode, 0)
        for field in ("max_input_tokens", "max_output_tokens"):
            candidate = values_with_metadata()
            candidate["gatewayModelMetadata"]["models"][0]["limits"][field] = 8193
            self.assertIn("individually not exceed", render("agentgateway", candidate, check=False).stderr)
        values["gatewayModelMetadata"]["models"][0]["limits"] = {
            "context_tokens": 9007199254740992, "max_input_tokens": 9007199254740993,
            "max_output_tokens": None,
        }
        self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)

    def test_strict_source_fields_and_timestamp_structure(self):
        for section, field, bad in (
            (None, "gateway_restrictions", {}),
            (None, "extra", True),
            ("limits", "enforced", True),
            ("capabilities", "extra", "supported"),
            ("capabilities", "streaming", True),
            ("capabilities", "streaming", "maybe"),
            ("provenance", "kind", "inferred"),
            ("provenance", "source", "x" * 513),
            ("provenance", "observed_at", "2026-01-01"),
            ("provenance", "valid_until", "2026-01-01T00:00:00+01:00"),
            ("provenance", "observed_at", "2026-99-99T99:99:99Z"),
        ):
            values = values_with_metadata()
            row = values["gatewayModelMetadata"]["models"][0]
            (row[section] if section else row)[field] = bad
            with self.subTest(section=section, field=field, bad=bad):
                self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)

    def test_metadata_identity_requires_enabled_api_canonical_model(self):
        for binding, model in (("missing", "example-chat"), ("openai", "provider-chat"),
                               ("google", "example-gemini"), ("anthropic", "example-chat")):
            values = values_with_metadata()
            values["gatewayModelMetadata"]["models"] = [record(binding, model)]
            self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)
        values = values_with_metadata()
        values["external-api-providers"][0]["enabled"] = False
        self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)
        values = values_with_metadata()
        values["gatewayModelMetadata"]["models"] *= 2
        self.assertIn("pairs must be unique", render("agentgateway", values, check=False).stderr)

    def test_record_bound_accepts_256_unique_models_and_rejects_257(self):
        values = capacity_values(256)
        values["gatewayModelMetadata"] = {
            "enabled": True, "models": [record(f"provider-{i}", f"model/{i}") for i in range(256)],
        }
        self.assertEqual(len(metadata(render("agentgateway", values))["models"]), 256)
        values["gatewayModelMetadata"]["models"].append(record("provider-256", "model/256"))
        self.assertNotEqual(render("agentgateway", values, check=False).returncode, 0)

    def test_serialized_document_size_is_bounded_after_json_escaping(self):
        values = capacity_values(256)
        rows = []
        for i, binding in enumerate(values["external-api-providers"]):
            public_id = f"model/{i}/" + "x" * 110
            binding["models"][0]["name"] = public_id
            values["authKeycloak"]["agentgatewayClientRoles"].append(f"model:{public_id}:invoke")
            row = record(binding["id"], public_id)
            row["provenance"]["source"] = "<" * 512
            rows.append(row)
        values["gatewayModelMetadata"] = {"enabled": True, "models": rows}
        failed = render("agentgateway", values, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("metadata exceeds 1 MiB", failed.stderr)

    def test_policy_restrictions_are_separate_and_survive_aliases_and_reroutes(self):
        values = values_with_metadata()
        row = values["gatewayModelMetadata"]["models"][0]
        row["capabilities"] = dict.fromkeys(CAPABILITIES, "supported")
        values["external-api-providers"][0]["localReroute"] = "local-chat"
        values["external-api-providers"][0]["models"][0]["attachments"] = {
            "documents": {"mode": "extract-text"}, "images": {"mode": "extract-text"},
        }
        values["docling"] = {"enabled": True, "inference": {"mode": "cpu"}}
        result = metadata(render("agentgateway", values))["models"][0]
        self.assertEqual(result["capabilities"], row["capabilities"])
        self.assertEqual(result["gateway_restrictions"]["blocked_capabilities"], ["image_input", "document_input"])
        self.assertTrue(all("extract-text" in ref for ref in result["gateway_restrictions"]["evidence_refs"]))
        self.assertEqual(result["provenance"], row["provenance"])
        # Forward-normalized is not proof of unconditional image denial or support.
        local = copy.deepcopy(record("local", "local-chat"))
        values["gatewayModelMetadata"]["models"] = [local]
        model = values["self-hosted-api-providers"][0]["models"][0]
        model.update(supportsImages=True, attachments={"images": {"mode": "forward-normalized"}})
        result = metadata(render("agentgateway", values))["models"][0]
        self.assertNotIn("image_input", result["gateway_restrictions"]["blocked_capabilities"])
        self.assertEqual(result["capabilities"]["image_input"], "unknown")
