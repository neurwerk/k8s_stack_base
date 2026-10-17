"""The shared client limit reaches both services with matching validation."""

import unittest

from helm import env_value, render, values_from


class ReasoningLimitTest(unittest.TestCase):
    def test_shared_client_value_reaches_both_services(self):
        for chart, variable in (
            ("agentgateway-extproc", "EXTPROC_MAX_REASONING_FINGERPRINTS"),
            ("pii-engine", "PII_ENGINE_MAX_REASONING_FINGERPRINTS"),
        ):
            for values, expected in (({}, "4096"), ({"reasoning": {"maxFingerprints": 8192}}, "8192")):
                with self.subTest(chart=chart, values=values):
                    self.assertEqual(env_value(render(chart, values), variable), expected)
            self.assertIn(("ConfigMap", "client-values"), values_from(f"{chart}/app.yaml"))

    def test_invalid_client_limit_is_rejected_by_both_charts(self):
        for chart in ("agentgateway-extproc", "pii-engine"):
            for value in (0, -1, 1.5, "8192", True, None):
                with self.subTest(chart=chart, value=value):
                    result = render(chart, {"reasoning": {"maxFingerprints": value}}, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("maxFingerprints", result.stderr)
