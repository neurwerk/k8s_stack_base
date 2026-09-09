"""Release gate and executable permission adapter contracts."""

import subprocess
import unittest
from pathlib import Path

from .helpers import ROOT, render_chart, resource, secret_ref_names
from .test_shared_config import render_librechat_config


class PermissionsTests(unittest.TestCase):
    def test_runner(self) -> None:
        subprocess.run(
            ["node", "--test", str(Path(__file__).with_name("permissions.test.cjs"))],
            check=True, timeout=30,
        )

    def test_hook_prerequisites_isolation_and_deadlines(self) -> None:
        manifest = render_chart("app", release_name="frontend-librechat").stdout
        name = "frontend-librechat-permissions"
        job = resource(manifest, "Job", name)
        for kind in ("ServiceAccount", "NetworkPolicy"):
            prerequisite = resource(manifest, kind, name)
            self.assertIn('helm.sh/hook-weight: "-20"', prerequisite)
            self.assertIn("helm.sh/hook: pre-install,pre-upgrade", prerequisite)
            self.assertIn("helm.sh/hook-delete-policy: before-hook-creation\n", prerequisite)
        self.assertIn('helm.sh/hook-weight: "-10"', job)
        self.assertIn("activeDeadlineSeconds: 300", job)
        self.assertIn("backoffLimit: 0", job)
        self.assertIn("before-hook-creation,hook-succeeded", job)
        self.assertIn("automountServiceAccountToken: false", job)
        self.assertIn("app.kubernetes.io/instance: frontend-librechat-permissions", job)
        self.assertEqual(secret_ref_names(job), {"frontend-librechat-secret"})
        self.assertIn("key: MONGO_URI", job)
        self.assertIn("key: REDIS_URI", job)
        self.assertNotIn("key: OPENID_CLIENT_SECRET", job)
        self.assertNotIn("name: config\n", job)
        service = resource(manifest, "Service", "frontend-librechat")
        self.assertNotIn("frontend-librechat-permissions", service)
        network = resource(manifest, "NetworkPolicy", name)
        self.assertNotIn("ipBlock", network)
        self.assertNotIn("traefik", network)
        self.assertIn("frontend-librechat-valkey", network)
        ingress = (ROOT / "charts/postgres/operations/templates/network-policy.yaml").read_text()
        self.assertIn("app.kubernetes.io/instance: frontend-librechat-permissions", ingress)
        release = (ROOT / "releases/librechat/core/app.yaml").read_text()
        for dependency in ("postgres-operations", "frontend-librechat-shared", "frontend-librechat-valkey"):
            self.assertIn(f"- name: {dependency}", release)

    def test_endpoints_enabled_without_global_role_overrides(self) -> None:
        config = render_librechat_config()
        interface = config.split("interface:\n", 1)[1].split("modelSpecs:", 1)[0]
        self.assertNotRegex(interface, r"(?m)^  (agents|marketplace):")
        deployment = resource(render_chart("app").stdout, "Deployment", "frontend-librechat")
        self.assertIn("- name: ENDPOINTS\n              value: custom,agents", deployment)
