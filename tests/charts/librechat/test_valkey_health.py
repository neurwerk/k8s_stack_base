"""LibreChat's Valkey outage recovery contract."""

import subprocess
import unittest
from pathlib import Path

from .helpers import render_chart, resource


class ValkeyHealthTests(unittest.TestCase):
    def test_recovery_state_machine(self) -> None:
        subprocess.run(
            ["node", "--test", str(Path(__file__).with_name("valkey-health.test.cjs"))],
            check=True, timeout=30,
        )

    def test_probes_and_script_are_deployed_together(self) -> None:
        manifest = render_chart("app").stdout
        deployment = resource(manifest, "Deployment", "frontend-librechat")
        config = resource(manifest, "ConfigMap", "frontend-librechat-valkey-health")
        self.assertIn("valkey-health.cjs: |", config)
        for mode in ("startup", "readiness", "liveness"):
            self.assertIn(f"command: [node, /app/valkey-health.cjs, {mode}]", deployment)
        self.assertIn("checksum/valkey-health:", deployment)
        self.assertIn("mountPath: /app/valkey-health.cjs", deployment)
        self.assertIn("name: frontend-librechat-valkey-health", deployment)
