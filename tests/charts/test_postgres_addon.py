"""Prevent an add-on from taking over Base-owned PostgreSQL roles."""

import unittest

from helm import render, resource


class AddonDatabaseTests(unittest.TestCase):
    def test_retry_records_intent_before_nontransactional_creation(self):
        job = resource(render("postgres/addon", {
            "addon": {
                "role": "example", "passwordSecret": "example-password",
                "databases": [{"name": "example_data", "vector": True}],
            },
        }, namespace="infra-postgres-operations", release="example"), "Job")
        script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
        self.assertLess(script.index("pg_advisory_lock("), script.index("BEGIN;"))
        self.assertLess(script.index("CREATE ROLE %I"), script.index("COMMIT;"))
        self.assertLess(script.index("VALUES ('example_data', 'example', 'pending')"), script.index("COMMIT;"))
        self.assertLess(script.index("COMMIT;"), script.index("CREATE DATABASE example_data OWNER example"))
        self.assertLess(script.index("COMMENT ON DATABASE example_data"), script.index("SET state = 'ready'"))
        self.assertLess(script.index("pg_advisory_unlock("), script.index("CREATE EXTENSION IF NOT EXISTS vector"))
        self.assertIn("RAISE EXCEPTION 'Unrecorded addon database already exists'", script)
        self.assertIn("RAISE EXCEPTION 'Unrecorded addon role already exists'", script)
        self.assertIn("l.state = 'pending'", script)
        self.assertIn("s.description IS NOT NULL OR NOT EXISTS", script)
        self.assertIn("r.rolname != 'example'", script)

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
