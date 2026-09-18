"""Exercise the shipped shell/jq gate with synthetic Rook status snapshots."""

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import textwrap
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts/rook-ceph"
IMAGE = "quay.io/ceph/ceph:v20.2.4"
COMPONENTS = ("admin", "mon", "mgr", "osd", "crashCollector", "cephExporter")
WARNINGS = (
    "AUTH_INSECURE_ROTATING_SERVICE_KEY_TYPE", "AUTH_INSECURE_CLIENT_KEY_TYPE",
    "AUTH_INSECURE_KEYS_ALLOWED", "AUTH_INSECURE_KEYS_CREATABLE",
)


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time())
        self.key = {"keyGeneration": 2, "keyCephVersion": "20.2.4-0"}
        self.cluster = {
            "metadata": {"uid": "cluster-fixture", "generation": 3},
            "spec": {"cephVersion": {"image": IMAGE}, "security": {"cephx": {
                "daemon": {"keyRotationPolicy": "KeyGeneration", "keyGeneration": 2},
                "csi": {"keyType": "aes"},
            }}},
            "status": {
                "phase": "Ready", "observedGeneration": 3,
                "version": {"image": IMAGE, "version": "20.2.4-0"},
                "cephx": {name: copy.deepcopy(self.key) for name in COMPONENTS},
                "ceph": {"health": "HEALTH_OK", "lastChecked": timestamp(self.now - 1)},
            },
        }
        self.store = {
            "metadata": {"uid": "store-fixture", "generation": 1},
            "status": {"phase": "Ready", "observedGeneration": 1,
                       "cephx": {"daemon": copy.deepcopy(self.key)}},
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
                 "OBJECT_STORE": "fixture", "CEPH_IMAGE": IMAGE, "DAEMON_KEY_GENERATION": "2",
                 "CLUSTER_JSON": json.dumps(self.cluster) if cluster is None else cluster,
                 "STORE_JSON": json.dumps(self.store), "CLOCK": str(self.now - 2), **env},
        )
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_migrated_and_fresh_keys_without_key_type(self):
        self.run_gate()
        for key in self.cluster["status"]["cephx"].values():
            key["keyGeneration"] = 4
        self.store["status"]["cephx"]["daemon"]["keyGeneration"] = 4
        self.run_gate()

    def test_future_patch_does_not_require_rotating_secure_keys(self):
        image = "quay.io/ceph/ceph:v20.2.5"
        self.cluster["spec"]["cephVersion"]["image"] = image
        self.cluster["status"]["version"] = {"image": image, "version": "20.2.5-0"}
        self.run_gate(CEPH_IMAGE=image)

    def test_exact_warning_allowlist(self):
        health = self.cluster["status"]["ceph"]
        health["health"] = "HEALTH_WARN"
        for codes in [(code,) for code in WARNINGS] + [WARNINGS]:
            with self.subTest(codes=codes):
                health["details"] = {code: {"severity": "HEALTH_WARN"} for code in codes}
                result = self.run_gate()
                for code in codes:
                    self.assertIn(code, result.stdout)
        for code in ("OSD_DOWN", "BLUESTORE_SLOW_OP_ALERT", "AUTH_UNKNOWN",
                     "AUTH_INSECURE_SERVICE_KEY_TYPE", "AUTH_INSECURE_SERVICE_TICKETS"):
            with self.subTest(code=code):
                health["details"] = {WARNINGS[0]: {"severity": "HEALTH_WARN"},
                                     code: {"severity": "HEALTH_WARN"}}
                self.run_gate(False)

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

    def test_each_managed_key_and_rgw_must_be_migrated(self):
        for keys, component in [(self.cluster["status"]["cephx"], c) for c in COMPONENTS] + [
            (self.store["status"]["cephx"], "daemon")
        ]:
            for value in ({}, {**self.key, "keyGeneration": 1},
                          {**self.key, "keyGeneration": "2"},
                          {**self.key, "keyCephVersion": ""},
                          {**self.key, "keyCephVersion": "Uninitialized"},
                          {**self.key, "keyCephVersion": "20.2.2-0"}):
                with self.subTest(component=component, value=value):
                    keys[component] = value
                    self.run_gate(False)
            keys[component] = copy.deepcopy(self.key)

    def test_current_resources_target_and_read_failures(self):
        for resource in (self.cluster, self.store):
            resource["status"]["observedGeneration"] -= 1
            self.run_gate(False)
            resource["status"]["observedGeneration"] += 1
            resource["metadata"]["deletionTimestamp"] = timestamp(self.now)
            self.run_gate(False)
            del resource["metadata"]["deletionTimestamp"]
        for field in ("image", "version"):
            previous = self.cluster["status"]["version"][field]
            self.cluster["status"]["version"][field] = "wrong"
            self.run_gate(False)
            self.cluster["status"]["version"][field] = previous
        for value in ("", "null", "{invalid", "{}"):
            self.run_gate(False, cluster=value)
        for resource in ("cephcluster", "cephobjectstore"):
            self.run_gate(False, READ_FAIL=resource)
        self.cluster["spec"]["security"]["cephx"]["csi"]["keyType"] = "aes256k"
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

    def test_migration_regression_resets_barrier(self):
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
    def render(self, generation):
        return subprocess.run(
            ["helm", "template", "rook-ceph", str(CHART), "--values",
             str(ROOT / "tests/validation/helm-lint-values.yaml"),
             "--set-json", "infraRookCeph.daemonKeyGeneration=" + json.dumps(generation)],
            text=True, capture_output=True, timeout=30,
        )

    def test_generation_range_and_shipped_script(self):
        for value in (1, 2, 4294967295):
            result = self.render(value)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"keyGeneration: {value}", result.stdout)
        for value in (0, -1, 4294967296, 1.5, True, None, "two", "2", "02", "2.5", [], {}):
            with self.subTest(value=value):
                result = self.render(value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("daemonKeyGeneration must be a positive integer", result.stderr)
        result = self.render(2)
        self.assertIn("csi:\n        keyType: aes", result.stdout)
        self.assertIn(IMAGE, result.stdout)
        self.assertIn("quay.io/cephcsi/cephcsi:v3.17.0", result.stdout)
        self.assertNotIn("muteHealthWarning", result.stdout)
        self.assertNotIn("allowedCiphers", result.stdout)
        script = (CHART / "files/health.sh").read_text()
        self.assertIn(script.strip(), "\n".join(line[15:] if line.startswith(" " * 15) else line
                                               for line in result.stdout.splitlines()))
        job = result.stdout.split("# Source: rook-ceph/templates/readiness-job.yaml\n", 1)[1]
        shell = textwrap.dedent(job.split("            - |\n", 1)[1].split("\n          env:", 1)[0])
        syntax = subprocess.run(["/bin/sh", "-n"], input=shell, text=True, capture_output=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertEqual(shell.count("\nEOF\n"), 2, "Smoke-test heredocs must remain unindented")
        template = (CHART / "templates/readiness-job.yaml").read_text()
        self.assertLess(template.index("RBD dynamic provisioning and persisted write/read succeeded."),
                        template.index("Ceph health did not pass after the RBD smoke test."))
