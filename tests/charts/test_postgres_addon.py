"""Prevent an add-on from taking over Base-owned PostgreSQL roles."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helm import render, resource


class OperationsStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.default = render("postgres/operations")
        cls.custom = render("postgres/operations", {
            "postgresOperations": {
                "maxConnections": 150,
                "maxLocksPerTransaction": 1024,
                "pgCron": {"logRun": True},
            },
        })

    def invoke(self, rendered, arguments):
        script = resource(rendered, "ConfigMap", "postgres-operations-bootstrap")["data"]["pg-ctl.sh"]
        with tempfile.TemporaryDirectory() as directory:
            wrapper = Path(directory) / "wrapper.sh"
            wrapper.write_text(script)
            stub = Path(directory) / "pg_ctl"
            stub.write_text(
                f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n"
            )
            stub.chmod(0o755)
            result = subprocess.run(
                ["/bin/bash", str(wrapper), *arguments],
                env={**os.environ, "PATH": f"{directory}:{os.environ['PATH']}"},
                capture_output=True, text=True, check=True, timeout=15,
            )
        return json.loads(result.stdout)

    def test_start_preserves_upstream_options_and_applies_default_or_custom_settings(self):
        arguments = ["start", "-D", "/data/path with spaces", "-o", "-p 9712", "-w"]
        for rendered, connections, locks, cron in (
            (self.default, 200, 512, "off"),
            (self.custom, 150, 1024, "on"),
        ):
            with self.subTest(connections=connections, locks=locks, cron=cron):
                actual = self.invoke(rendered, arguments)
                self.assertEqual(actual[:len(arguments)], arguments)
                options = actual[len(arguments):]
                self.assertEqual(options[::2], ["-o", "-o", "-o"])
                self.assertCountEqual(options[1::2], [
                    f"-c cron.log_run={cron}",
                    f"-c max_connections={connections}",
                    f"-c max_locks_per_transaction={locks}",
                ])

    def test_non_start_invocations_are_unchanged(self):
        for arguments in ([], ["stop", "-D", "/data/path with spaces", "-m", "fast"],
                          ["status", "-D", "/data/postgresql"]):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.invoke(self.custom, arguments), arguments)

    def test_invalid_startup_limits_fail_before_rendering(self):
        for key, invalid in (
            ("maxConnections", (19, 501, "200", 200.5, True, None)),
            ("maxLocksPerTransaction", (63, 4097, "512", 512.5, False, None)),
        ):
            for value in invalid:
                with self.subTest(key=key, value=value):
                    result = render("postgres/operations", {
                        "postgresOperations": {key: value},
                    }, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(key, result.stderr)

    def test_each_startup_limit_changes_the_pod_restart_checksum(self):
        def checksum(rendered):
            statefulset = resource(rendered, "StatefulSet", "postgres-operations")
            return statefulset["spec"]["template"]["metadata"]["annotations"]["checksum/bootstrap"]

        original = checksum(self.default)
        for key, value in (("maxConnections", 150), ("maxLocksPerTransaction", 1024)):
            with self.subTest(key=key):
                changed = render("postgres/operations", {"postgresOperations": {key: value}})
                self.assertNotEqual(checksum(changed), original)


class AddonDatabaseTests(unittest.TestCase):
    def test_database_intent_commits_before_creation_and_unmarked_retry_needs_pending_owner(self):
        job = resource(render("postgres/addon", {
            "addon": {
                "role": "example",
                "passwordSecret": "example-postgres-values",
                "databases": [{"name": "example_data", "vector": False}],
            },
        }, namespace="infra-postgres-operations"), "Job")
        script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
        intent = "INSERT INTO addon_provisioning.databases (database_name, release_name, state)"
        create = "SELECT 'CREATE DATABASE example_data OWNER example'"
        self.assertLess(script.index(intent), script.index("COMMIT;", script.index(intent)))
        self.assertLess(script.index("COMMIT;", script.index(intent)), script.index(create))
        self.assertIn("s.description IS NOT NULL OR NOT EXISTS", script)
        self.assertIn("l.state = 'pending'", script)
        self.assertIn("r.rolname != 'example'", script)

    def test_same_name_role_and_database_retry_keeps_other_release_collision_guard(self):
        job = resource(render("postgres/addon", {
            "addon": {
                "role": "contextforge",
                "passwordSecret": "contextforge-postgres-values",
                "databases": [{"name": "contextforge", "vector": False}],
            },
        }, namespace="infra-postgres-operations"), "Job")
        script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
        self.assertIn(
            "WHERE database_name = 'contextforge' AND release_name != 'catalog-test'", script
        )
        self.assertNotIn("WHERE database_name = 'contextforge') OR", script)

    def test_base_owned_names_and_invalid_adoption_fail_before_rendering(self):
        for name in ("postgres", "agentgateway", "studio", "not-valid", "A"):
            with self.subTest(name=name):
                result = render("postgres/operations", {
                    "postgresOperationsAddon": {"name": name},
                }, check=False)
                self.assertNotEqual(result.returncode, 0)
        result = render("postgres/operations", {
            "postgresOperationsAddon": {"name": "example_app", "adoptExisting": "true"},
        }, check=False)
        self.assertNotEqual(result.returncode, 0)


class DatabaseIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scripts = {}
        for addon in ("", "example_app"):
            job = resource(render("postgres/operations", {
                "postgresOperationsAddon": {"name": addon},
            }), "Job", "postgres-operations-provision")
            script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
            cls.scripts[addon] = script[script.index("databases=$(psql"):]

    def verify(self, *, addon="", unexpected=None, discovery_failure=False):
        databases = ["postgres", "template1", "agentgateway", "postgres_langfuse",
                     "postgres_langfuse_v4", "studio", "api_key_bridge",
                     "other_addon", "addon with spaces"]
        if addon:
            databases.append(addon)
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / "psql"
            stub.write_text(f"#!{sys.executable}\n" + '''
import json
import os
import sys

if any("SELECT datname FROM pg_database" in arg for arg in sys.argv):
    if os.environ["DISCOVERY_FAILURE"] == "true":
        sys.exit(2)
    print("\\n".join(json.loads(os.environ["DATABASES"])))
    sys.exit(0)
user, database = os.environ["PGUSER"], os.environ["PGDATABASE"]
if "--command=SELECT 1" not in sys.argv:
    sys.exit(0)
with open(os.environ["ATTEMPTS"], "a") as stream:
    stream.write(json.dumps([user, database]) + "\\n")
allowed = {"agentgateway": ["agentgateway"],
           "langfuse": ["postgres_langfuse", "postgres_langfuse_v4"],
           "studio": ["studio"], "api_key_bridge": ["api_key_bridge"],
           "example_app": ["example_app"]}
unexpected = json.loads(os.environ["UNEXPECTED"])
sys.exit(0 if database in allowed.get(user, []) or [user, database] == unexpected else 1)
''')
            stub.chmod(0o755)
            attempts = Path(directory) / "attempts.jsonl"
            env = {
                **os.environ, "PATH": f"{directory}:{os.environ['PATH']}",
                "PGUSER": "documentdb", "PGDATABASE": "postgres",
                "DATABASES": json.dumps(databases), "UNEXPECTED": json.dumps(unexpected),
                "DISCOVERY_FAILURE": str(discovery_failure).lower(), "ATTEMPTS": str(attempts),
                "ADDON_DATABASE_NAME": addon,
                **{name: "synthetic-test-password" for name in (
                    "AGENTGATEWAY_PASSWORD", "LANGFUSE_PASSWORD", "STUDIO_PASSWORD",
                    "API_KEY_BRIDGE_PASSWORD", "ADDON_DATABASE_PASSWORD",
                )},
            }
            result = subprocess.run(["/bin/sh", "-ec", self.scripts[addon]],
                                    env=env, capture_output=True, text=True, timeout=15)
            calls = [json.loads(line) for line in attempts.read_text().splitlines()] if attempts.exists() else []
        return result, calls

    def test_isolated_roles_pass_and_all_existing_databases_are_checked(self):
        result, calls = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        for user in ("agentgateway", "langfuse", "studio", "api_key_bridge"):
            for database in ("other_addon", "addon with spaces", "postgres", "template1"):
                self.assertIn([user, database], calls)
        self.assertNotIn(["langfuse", "postgres_langfuse"], calls)
        self.assertNotIn(["langfuse", "postgres_langfuse_v4"], calls)

    def test_unconfigured_addon_access_fails_for_each_base_role(self):
        for user in ("agentgateway", "langfuse", "studio", "api_key_bridge"):
            with self.subTest(user=user):
                result, _ = self.verify(unexpected=[user, "other_addon"])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"{user} unexpectedly connected", result.stderr)

    def test_configured_addon_is_checked_against_other_addons(self):
        result, calls = self.verify(addon="example_app")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["example_app", "other_addon"], calls)
        result, _ = self.verify(addon="example_app", unexpected=["example_app", "other_addon"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("example_app unexpectedly connected", result.stderr)

    def test_database_discovery_failure_aborts_verification(self):
        result, calls = self.verify(discovery_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertNotIn("provisioning verified", result.stdout)
