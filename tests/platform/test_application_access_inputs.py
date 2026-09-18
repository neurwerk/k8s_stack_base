"""Source selectors are data, not commands, and malformed input stays private."""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts import application_access_inputs as inputs

ROOT = Path(__file__).resolve().parents[2]


def source(ref):
    return yaml.safe_dump(
        {
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "GitRepository",
            "metadata": {"name": "k8s-stack", "namespace": "flux-system"},
            "spec": {"url": "https://github.com/neurwerk/k8s_stack_base.git", "ref": ref},
        }
    )


class ApplicationAccessInputsTests(unittest.TestCase):
    def test_selectors_are_exact_and_not_executable(self):
        for ref, expected in (
            ({"branch": "main"}, "refs/heads/main"),
            ({"tag": "v1.2.3"}, "refs/tags/v1.2.3"),
            ({"commit": "a" * 40}, "a" * 40),
        ):
            with self.subTest(ref=ref):
                self.assertEqual(inputs.platform_ref(source(ref)), expected)
        for ref in (
            {"branch": "candidate"},
            {"tag": "v01.2.3"},
            {"tag": "v1.2.3-rc.1"},
            {"commit": "abc123"},
            {"commit": "A" * 40},
            {"commit": "a" * 64},
            {"commit": "$(command)"},
            {"branch": "main", "commit": "a" * 40},
            {"tag": 123},
        ):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                inputs.platform_ref(source(ref))

    def test_malformed_yaml_and_identity_failures_are_sanitized(self):
        marker = "sensitive-payload.example.com"
        documents = {
            "duplicate": source({"branch": "main"}) + "spec: " + marker,
            "alias": "spec: &private " + marker + "\nmetadata: *private",
            "explicit tag": "!!str " + marker,
            "nonstring key": "123: " + marker,
            "bad YAML": "spec: [" + marker,
            "bad date": "spec: 2026-99-99\n# " + marker,
            "huge integer": "spec: " + "9" * 5000 + "\n# " + marker,
            "deep nesting": "spec: " + "[" * 1500 + marker + "]" * 1500,
            "URL": source({"branch": "main"}).replace("github.com", marker),
            "identity": source({"branch": "main"}).replace("name: k8s-stack", "name: " + marker),
        }
        for name, content in documents.items():
            with self.subTest(name=name), self.assertRaises(ValueError) as raised:
                inputs.platform_ref(content)
            self.assertNotIn(marker, str(raised.exception))

    def test_cli_reads_only_explicit_client_source_and_sanitizes_failures(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            path = root / "clusters/prod-eu-1/platform-source.yaml"
            path.parent.mkdir(parents=True)
            path.write_text(source({"commit": "a" * 40}))
            output = io.StringIO()
            with redirect_stdout(output):
                inputs.main(["--client-root", str(root), "--field", "platform_ref"])
            self.assertEqual(output.getvalue(), "a" * 40 + "\n")
        for error in (
            OSError("private-path"),
            UnicodeError("private-content"),
            RecursionError("private-content"),
        ):
            stderr = io.StringIO()
            with (
                patch.object(Path, "read_text", side_effect=error),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit) as raised,
            ):
                inputs.main(["--client-root", "/example/client", "--field", "platform_ref"])
            self.assertEqual(raised.exception.code, 1)
            self.assertNotIn("private-", stderr.getvalue())
            self.assertIn("details withheld", stderr.getvalue())
        for args in (
            ["--field", "platform_ref"],
            ["--client-root", "/example/client", "--field", "checker_ref"],
            ["--client-root", "/example/client"],
        ):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                inputs.main(args)
            self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
