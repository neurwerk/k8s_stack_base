"""Production validation contracts for LibreChat Code Interpreter."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from .helpers import render_chart


NAMESPACE = "librechat-code-interpreter"


def render_code_interpreter(
    chart: str,
    *,
    values: tuple[Path, ...] = (),
    platform_values: bool = True,
    extra_args: tuple[str, ...] = (),
    check: bool = True,
):
    """Render one Code Interpreter leaf chart with its deployed identity."""
    name = f"librechat-code-interpreter-{Path(chart).name}"
    return render_chart(
        f"code-interpreter/{chart}",
        release_name=name,
        namespace=NAMESPACE,
        values=values,
        platform_values=platform_values,
        extra_args=extra_args,
        check=check,
    )


class CodeInterpreterProductionValidationTests(unittest.TestCase):
    """Require production package persistence and initialization."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_directory = tempfile.TemporaryDirectory(prefix="code-interpreter-")
        cls.temp_path = Path(cls.temp_directory.name)
        cls.valid_worker_values = cls.write_values(
            "valid-worker.yaml",
            """frontendLibrechatCodeInterpreter:
  validation:
    production: true
  scheduling:
    nodeSelector:
      code-interpreter.neurwerk.com/dedicated: "true"
    tolerations:
      - key: code-interpreter.neurwerk.com/dedicated
        operator: Equal
        value: "true"
        effect: NoSchedule
  packages:
    persistence:
      storageClassName: test-rbd
""",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_directory.cleanup()

    @classmethod
    def write_values(cls, filename: str, content: str) -> Path:
        path = cls.temp_path / filename
        path.write_text(content, encoding="ascii")
        return path

    def test_production_rejects_disabled_package_storage(self) -> None:
        result = render_code_interpreter(
            "shared",
            extra_args=(
                "--set",
                "frontendLibrechatCodeInterpreter.packages.persistence.enabled=false",
            ),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("packages.persistence.enabled must remain true", result.stderr)

    def test_production_rejects_disabled_package_initialization(self) -> None:
        result = render_code_interpreter(
            "package-init",
            values=(self.valid_worker_values,),
            platform_values=False,
            extra_args=(
                "--set",
                "frontendLibrechatCodeInterpreter.packages.initJob.enabled=false",
            ),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("packages.initJob.enabled must remain true", result.stderr)

    def test_package_storage_class_is_required_in_production(self) -> None:
        result = render_code_interpreter(
            "shared",
            extra_args=(
                "--set-string",
                "frontendLibrechatCodeInterpreter.packages.persistence.storageClassName=",
            ),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "packages.persistence.storageClassName is required from client-owned values",
            result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
