"""Model source policy rendering and rejection at the chart boundary."""

import unittest

import yaml

from helm import render, resource


class ModelSourcePolicyTests(unittest.TestCase):
    def test_model_rules_survive_rendering(self):
        policies = {"approved/model": [{"entityType": "MCP_TOOL_DEFAULT", "action": "pass"}]}
        rendered = render("pii-engine", {
            "monitorPiiEngine": {"policy": {"pii": {"modelSourcePolicies": policies}}},
        })
        data = resource(rendered, "ConfigMap", "monitor-pii-engine-config-map")["data"]
        self.assertEqual(yaml.safe_load(data["policy.yaml"])["pii"]["modelSourcePolicies"], policies)

    def test_invalid_model_rules_fail_rendering(self):
        for policies in (
            {"approved/*": []},
            {"approved/model": [{"entityType": "EMAIL_ADDRESS", "action": "pass"}]},
            {"approved/model": [{"entityType": "MCP_TOOL_A", "action": "mask"}]},
            {"approved/model": [{"entityType": "MCP_TOOL_A", "action": "pass", "routeClass": "x"}]},
            {"approved/model": [{"entityType": "MCP_TOOL_A", "action": "pass"}] * 2},
        ):
            with self.subTest(policies=policies), self.assertRaises(AssertionError):
                render("pii-engine", {
                    "monitorPiiEngine": {"policy": {"pii": {"modelSourcePolicies": policies}}},
                })
