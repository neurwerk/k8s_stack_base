"""Exercise the shipped shell/jq gate with synthetic Rook status snapshots."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import textwrap
import time
import unittest
from helm import render

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts/rook-ceph"
IMAGE = "quay.io/ceph/ceph:v20.2.2"
WARNINGS = (
    "AUTH_INSECURE_ROTATING_SERVICE_KEY_TYPE",
    "AUTH_INSECURE_CLIENT_KEY_TYPE",
    "AUTH_INSECURE_KEYS_ALLOWED",
    "AUTH_INSECURE_KEYS_CREATABLE",
)


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time())
        self.cluster = {
            "metadata": {"uid": "cluster-fixture", "generation": 3},
            "spec": {"cephVersion": {"image": IMAGE}},
            "status": {
                "phase": "Ready", "observedGeneration": 3,
                "version": {"image": IMAGE, "version": "20.2.2-0"},
                "ceph": {"health": "HEALTH_OK", "lastChecked": timestamp(self.now - 1)},
            },
        }
        self.store = {
            "metadata": {"uid": "store-fixture", "generation": 1},
            "status": {"phase": "Ready", "observedGeneration": 1},
        }

    def run_gate(self, success=True, *, cluster=None, body=None, **env):
        script = (CHART / "files/health.sh").read_text()
        script += '''
kubectl() {
  [ "${READ_FAIL:-}" != "$4" ] || return 1
  case "$4" in
    cephcluster) printf '%s' "$CLUSTER_JSON" ;;
    cephobjectstore) printf '%s' "$STORE_JSON" ;;
    *) return 1 ;;
  esac
}
date() { printf '%s' "$CLOCK"; }
'''
        script += body or '''
if ceph_health_ready; then exit 90; fi
ceph_health_ready
'''
        result = subprocess.run(
            ["/bin/sh", "-ec", script], capture_output=True, text=True, timeout=10,
            env={**os.environ, "POD_NAMESPACE": "fixture", "CEPH_CLUSTER": "fixture",
                 "OBJECT_STORE": "fixture", "CEPH_IMAGE": IMAGE,
                 "CLUSTER_JSON": json.dumps(self.cluster) if cluster is None else cluster,
                 "STORE_JSON": json.dumps(self.store), "CLOCK": str(self.now - 2), **env},
        )
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_ready_and_fresh_without_key_generation(self):
        self.run_gate()
        self.cluster["status"]["ceph"]["details"] = {}
        self.run_gate()

    def test_all_warnings_allowed(self):
        health = self.cluster["status"]["ceph"]
        health["health"] = "HEALTH_WARN"
        for codes in [(code,) for code in WARNINGS] + [WARNINGS]:
            with self.subTest(codes=codes):
                health["details"] = {code: {"severity": "HEALTH_WARN"} for code in codes}
                self.run_gate()
        for code in ("OSD_DOWN", "BLUESTORE_SLOW_OP_ALERT", "AUTH_UNKNOWN",
                     "AUTH_INSECURE_SERVICE_KEY_TYPE", "AUTH_INSECURE_SERVICE_TICKETS"):
            with self.subTest(code=code):
                health["details"] = {WARNINGS[0]: {"severity": "HEALTH_WARN"},
                                     code: {"severity": "HEALTH_WARN"}}
                self.run_gate()

    def test_errors_and_malformed_health(self):
        health = self.cluster["status"]["ceph"]
        for state, details in [
            ("HEALTH_ERR", {}), ("", {}), ("HEALTH_WARN", {}),
            ("HEALTH_WARN", None), ("HEALTH_WARN", []),
            ("HEALTH_WARN", {WARNINGS[0]: {}}),
            ("HEALTH_WARN", {WARNINGS[0]: {"severity": "HEALTH_ERR"}}),
            ("HEALTH_OK", {WARNINGS[0]: {"severity": "HEALTH_WARN"}}),
        ]:
            with self.subTest(state=state, details=details):
                health.update(health=state, details=details)
                self.run_gate(False)

    def test_current_resources_target_and_read_failures(self):
        for resource in (self.cluster, self.store):
            resource["status"]["observedGeneration"] -= 1
            self.run_gate(False)
            resource["status"]["observedGeneration"] += 1
            resource["metadata"]["deletionTimestamp"] = timestamp(self.now)
            self.run_gate(False)
            del resource["metadata"]["deletionTimestamp"]
            resource["status"]["phase"] = "Progressing"
            self.run_gate(False)
            resource["status"]["phase"] = "Ready"
        for field in ("image", "version"):
            previous = self.cluster["status"]["version"][field]
            self.cluster["status"]["version"][field] = "wrong"
            self.run_gate(False)
            self.cluster["status"]["version"][field] = previous
        for value in ("", "null", "{invalid", "{}"):
            self.run_gate(False, cluster=value)
            self.run_gate(False, STORE_JSON=value)
        for resource in ("cephcluster", "cephobjectstore"):
            self.run_gate(False, READ_FAIL=resource)
        self.cluster["spec"]["cephVersion"]["image"] = "wrong"
        self.run_gate(False)

    def test_fresh_sample_barrier_and_post_smoke_recheck(self):
        self.run_gate(body='''
if ceph_health_ready; then exit 90; fi
health_after=$((CLOCK + 1))
if ceph_health_ready; then exit 91; fi
health_after=$CLOCK
ceph_health_ready
# The Job resets this barrier after its RBD smoke test.
health_after=$((CLOCK + 1))
if ceph_health_ready; then exit 92; fi
''')
        for checked in (timestamp(self.now - 181), timestamp(self.now + 60), "invalid", None):
            self.cluster["status"]["ceph"]["lastChecked"] = checked
            self.run_gate(False)

    def test_status_regression_resets_barrier(self):
        self.run_gate(body='''
if ceph_health_ready; then exit 90; fi
ceph_health_ready
READ_FAIL=cephobjectstore
if ceph_health_ready; then exit 91; fi
READ_FAIL=
if ceph_health_ready; then exit 92; fi
ceph_health_ready
''')


class RenderTests(unittest.TestCase):
    def test_upgrade_safety_and_shipped_script(self):
        result = render("rook-ceph", release="rook-ceph")
        self.assertIn("- name: rook\n        enabled: true", result.stdout)
        self.assertIn("allowUnsupported: false", result.stdout)
        self.assertIn("skipUpgradeChecks: false", result.stdout)
        self.assertNotIn("keyGeneration", result.stdout)
        self.assertNotIn("DAEMON_KEY_GENERATION", result.stdout)
        self.assertNotIn("cephx:", result.stdout)
        self.assertNotIn("muteHealthWarning", result.stdout)
        self.assertNotIn("allowedCiphers", result.stdout)
        script = (CHART / "files/health.sh").read_text()
        self.assertIn(
            script.strip(),
            "\n".join(
                line[15:] if line.startswith(" " * 15) else line
                for line in result.stdout.splitlines()
            ),
        )
        job = result.stdout.split("# Source: rook-ceph/templates/readiness-job.yaml\n", 1)[1]
        shell = textwrap.dedent(
            job.split("            - |\n", 1)[1].split("\n          env:", 1)[0]
        )
        syntax = subprocess.run(["/bin/sh", "-n"], input=shell, text=True, capture_output=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertEqual(shell.count("\nEOF\n"), 2, "Smoke-test heredocs must remain unindented")
        template = (CHART / "templates/readiness-job.yaml").read_text()
        self.assertLess(
            template.index("RBD dynamic provisioning and persisted write/read succeeded."),
            template.index("Ceph health did not pass after the RBD smoke test."),
        )
