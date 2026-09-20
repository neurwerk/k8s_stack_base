"""Production package storage and initialization must not be disabled."""

import unittest

from helm import render


class CodeInterpreterProductionValidationTests(unittest.TestCase):
    def test_required_package_storage_and_initialization(self):
        for chart, setting, value, error in (
            ("shared", "persistence.enabled", False, "must remain true"),
            ("package-init", "initJob.enabled", False, "must remain true"),
            ("shared", "persistence.storageClassName", "", "is required from client-owned values"),
        ):
            with self.subTest(setting=setting):
                section, field = setting.split(".")
                result = render(
                    f"librechat/code-interpreter/{chart}",
                    {
                        "frontendLibrechatCodeInterpreter": {"packages": {section: {field: value}}},
                    },
                    namespace="librechat-code-interpreter",
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"packages.{setting} {error}", result.stderr)
