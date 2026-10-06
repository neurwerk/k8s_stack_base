"""Prevent an add-on from taking over Base-owned PostgreSQL roles."""

import unittest

from helm import render, resource


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
