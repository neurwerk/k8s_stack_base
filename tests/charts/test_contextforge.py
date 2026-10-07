"""Keep database OAuth independent of the unfinished Vault integration."""

import unittest

from helm import render, resource


class ContextForgeTests(unittest.TestCase):
    def test_database_oauth_never_requires_vault_token_with_or_without_proxy(self):
        for proxy in (False, True):
            with self.subTest(proxy=proxy):
                documents = render("contextforge", {
                    "contextforge": {
                        "trustedProxy": {
                            "enabled": proxy,
                            "studioOrigin": "https://studio.example.com",
                            "defaultUserRole": "reader",
                            "defaultTeamMemberRole": "member",
                        },
                    },
                }, namespace="contextforge", value_files=())
                for kind in ("Deployment", "Job"):
                    container = resource(documents, kind)["spec"]["template"]["spec"]["containers"][0]
                    environment = {entry["name"]: entry for entry in container["env"]}
                    self.assertEqual(environment["OAUTH_TOKEN_BACKEND"]["value"], "database")
                    self.assertNotIn("VAULT_TOKEN", environment)
                    self.assertIn("AUTH_ENCRYPTION_SECRET", environment)
