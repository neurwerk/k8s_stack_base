"""Prevent an add-on from taking over Base-owned PostgreSQL roles."""

import unittest

from helm import render


class AddonDatabaseTests(unittest.TestCase):
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
