"""Safety contracts for disposable postgres-operations live acceptance."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "tests/live/postgres/acceptance.py"
SPEC = importlib.util.spec_from_file_location("postgres_live_acceptance", RUNNER)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load postgres live acceptance runner")
acceptance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = acceptance
SPEC.loader.exec_module(acceptance)


class PostgresLiveAcceptanceTests(unittest.TestCase):
    def valid_environment(self, kubeconfig: str) -> dict[str, str]:
        return {
            "POSTGRES_LIVE_ACCEPTANCE_CONFIRM": acceptance.CONFIRMATION,
            "POSTGRES_LIVE_ACCEPTANCE_KUBECONFIG": kubeconfig,
            "POSTGRES_LIVE_ACCEPTANCE_EXPECTED_CONTEXT": "expected-context",
            "POSTGRES_LIVE_ACCEPTANCE_EXPECTED_CLIENT": "client_example_com",
            "POSTGRES_LIVE_ACCEPTANCE_STORAGE_CLASS": "infra-rook-ceph-rbd",
        }

    def test_requires_explicit_confirmation_and_kubeconfig(self) -> None:
        with tempfile.NamedTemporaryFile() as kubeconfig:
            environment = self.valid_environment(kubeconfig.name)
            environment.pop("POSTGRES_LIVE_ACCEPTANCE_CONFIRM")
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.load_config(environment)

            environment = self.valid_environment("relative-kubeconfig")
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.load_config(environment)

    def test_namespace_is_generated_and_scoped(self) -> None:
        first = acceptance.acceptance_namespace()
        second = acceptance.acceptance_namespace()
        self.assertTrue(first.startswith(acceptance.NAMESPACE_PREFIX))
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^[a-z0-9-]+$")

    def test_credentials_travel_in_values_stdin_not_helm_arguments(self) -> None:
        with tempfile.NamedTemporaryFile() as kubeconfig:
            config = acceptance.load_config(self.valid_environment(kubeconfig.name))
            passwords = acceptance.Passwords(
                "AdminAa1!", "DocumentAa1!", "DifyAa1!", "LangfuseAa1!", "RagAa1!"
            )
            values = json.loads(acceptance.values_json(config, passwords))
            command = acceptance.helm_command(config, "postgres-operations-acceptance-test")
        self.assertEqual(
            values["postgresOperationsSecrets"]["documentdbPassword"],
            passwords.documentdb,
        )
        rendered_command = " ".join(command)
        for password in passwords.__dict__.values():
            self.assertNotIn(password, rendered_command)
        self.assertIn("--values=-", command)
        self.assertNotIn("--wait", command)
        self.assertNotIn("--wait-for-jobs", command)

    def test_generated_passwords_meet_documentdb_complexity(self) -> None:
        passwords = acceptance.generate_passwords()
        for password in passwords.__dict__.values():
            self.assertRegex(password, r"[A-Z]")
            self.assertRegex(password, r"[a-z]")
            self.assertRegex(password, r"[0-9]")
            self.assertRegex(password, r"[^A-Za-z0-9]")

    def test_live_target_is_explicit_and_not_part_of_check(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        check_line = next(line for line in makefile.splitlines() if line.startswith("check:"))
        self.assertNotIn("live-postgres-acceptance", check_line)
        self.assertIn("live-postgres-acceptance:", makefile)
        self.assertIn("tests/live/postgres/acceptance.py", makefile)


if __name__ == "__main__":
    unittest.main()
