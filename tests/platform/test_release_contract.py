"""Release manifest contract tests."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "platform_release", ROOT / "scripts/platform_release.py"
)
assert SPEC and SPEC.loader
platform_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(platform_release)


class ReleaseContractTest(unittest.TestCase):
    def _release_integration_tag(self) -> str:
        tag = os.environ.get("PLATFORM_RELEASE_TEST_TAG", "")
        if not tag:
            self.skipTest(
                "release tag integration runs through make release-check TAG=vX.Y.Z"
            )
        return tag

    def test_manifest_matches_declared_schema(self) -> None:
        manifest = platform_release.build_manifest()
        platform_release.validate_manifest_schema(manifest)

    def test_stable_upgrade_policy_and_legacy_compatibility(self) -> None:
        scaffold = platform_release.migration_scaffold(
            "0.1.2", "supported", [], "forward-fix"
        )
        self.assertTrue(platform_release.contains_todo(scaffold))
        supported = scaffold.replace("TODO", "Reviewed evidence")
        self.assertIn("- Stable upgrades: Supported.", supported)
        self.assertIn("## Breaking Changes", supported)
        platform_release.validate_migration_compatibility(
            supported,
            {
                "stableUpgrade": "supported",
                "upgradesFromAlphaRevisions": [],
                "downgrade": "unsupported",
                "recovery": "forward-fix",
            },
        )
        with self.assertRaisesRegex(
            platform_release.ReleaseError, "exactly one ## Breaking Changes section"
        ):
            platform_release.validate_migration_compatibility(
                supported.replace("## Breaking Changes", "## Changes"),
                {
                    "stableUpgrade": "supported",
                    "upgradesFromAlphaRevisions": [],
                    "downgrade": "unsupported",
                    "recovery": "forward-fix",
                },
            )
        with self.assertRaisesRegex(
            platform_release.ReleaseError, "Breaking Changes section must not be empty"
        ):
            platform_release.validate_migration_compatibility(
                supported.replace(
                    "## Breaking Changes\n\nReviewed evidence.\n\n## Stateful",
                    "## Breaking Changes\n\n## Stateful",
                ),
                {
                    "stableUpgrade": "supported",
                    "upgradesFromAlphaRevisions": [],
                    "downgrade": "unsupported",
                    "recovery": "forward-fix",
                },
            )

        fresh_install_only = supported.replace(
            "Stable upgrades: Supported", "Stable upgrades: Fresh installation only"
        )
        fresh_policy = {
            "stableUpgrade": "fresh-install-only",
            "upgradesFromAlphaRevisions": [],
            "downgrade": "unsupported",
            "recovery": "forward-fix",
        }
        platform_release.validate_migration_compatibility(
            fresh_install_only, fresh_policy
        )
        manifest = platform_release.load_yaml(ROOT / "release/manifest.yaml")
        manifest["metadata"]["name"] = "v0.1.2"
        manifest["spec"]["version"] = "0.1.2"
        manifest["spec"]["compatibility"] = dict(fresh_policy)
        platform_release.validate_manifest_schema(manifest)
        manifest["spec"]["compatibility"]["upgradesFrom"] = []
        with self.assertRaisesRegex(
            platform_release.ReleaseError, "does not match its schema"
        ):
            platform_release.validate_manifest_schema(manifest)
        manifest["spec"]["compatibility"] = {
            "upgradesFrom": [],
            "upgradesFromAlphaRevisions": [],
            "downgrade": "unsupported",
            "recovery": "forward-fix",
        }
        with self.assertRaisesRegex(
            platform_release.ReleaseError, "does not match its schema"
        ):
            platform_release.validate_manifest_schema(manifest)
        with self.assertRaisesRegex(
            platform_release.ReleaseError, "stableUpgrade does not match"
        ):
            platform_release.validate_migration_compatibility(supported, fresh_policy)

        legacy = (ROOT / "release/migrations/v0.1.0.md").read_text()
        legacy_manifest = yaml.safe_load(
            platform_release.git("show", "v0.1.0:release/manifest.yaml")
        )
        platform_release.validate_manifest_schema(legacy_manifest)
        platform_release.validate_migration_compatibility(
            legacy,
            {
                "freshInstall": "supported",
                "upgradesFrom": [],
                "downgrade": "unsupported",
                "recovery": "replacement-restore",
            },
            require_alpha_revisions=False,
        )

    def test_migration_compatibility_rejects_invalid_alpha_revisions(self) -> None:
        migration = """## Support

- Stable upgrades: Supported.
- Supported alpha source revisions: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`, `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`.
- Downgrade: Unsupported.

## Breaking Changes

None.

## Recovery

Recovery classification: Forward fix.
"""
        invalid_migrations = (
            (
                "duplicate declaration",
                migration.replace(
                    "- Supported alpha source revisions:",
                    "- Supported alpha source revisions: None.\n"
                    "- Supported alpha source revisions:",
                ),
                "exactly one supported alpha source revisions declaration",
            ),
            (
                "short revision",
                migration.replace("`aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`", "`abc123`"),
                "comma-separated backticked full lowercase commits",
            ),
            (
                "uppercase revision",
                migration.replace("a" * 40, "A" * 40),
                "comma-separated backticked full lowercase commits",
            ),
            (
                "duplicate revisions",
                migration.replace("b" * 40, "a" * 40),
                "contain duplicate commits",
            ),
            (
                "misplaced declaration",
                migration.replace(
                    "- Supported alpha source revisions: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`, `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`.\n",
                    "",
                ).replace(
                    "Recovery classification: Forward fix.",
                    "- Supported alpha source revisions: None.\n"
                    "Recovery classification: Forward fix.",
                ),
                "declaration must appear in ## Support",
            ),
        )
        for name, candidate, message in invalid_migrations:
            with self.subTest(name=name):
                with self.assertRaisesRegex(platform_release.ReleaseError, message):
                    platform_release.parse_migration_compatibility(candidate)

    def test_migration_compatibility_allows_historical_missing_alpha_declaration(
        self,
    ) -> None:
        migration = """## Support

- Fresh installation: Supported.
- Supported source versions: None.
- Downgrade: Unsupported.

## Recovery

Recovery classification: Forward fix.
"""
        compatibility = platform_release.parse_migration_compatibility(
            migration, False, legacy=True
        )
        self.assertEqual(compatibility["upgradesFromAlphaRevisions"], [])
        platform_release.validate_migration_compatibility(
            migration,
            {
                "upgradesFrom": [],
                "downgrade": "unsupported",
                "recovery": "forward-fix",
            },
            require_alpha_revisions=False,
        )

        with self.assertRaisesRegex(
            platform_release.ReleaseError,
            "exactly one supported alpha source revisions declaration",
        ):
            platform_release.parse_migration_compatibility(
                migration, True, legacy=True
            )

    def test_current_commit_resolves_to_one_release_tag(self) -> None:
        tag = self._release_integration_tag()
        commit = platform_release.git("rev-parse", f"{tag}^{{commit}}")
        self.assertEqual(platform_release.release_tag_for_commit(commit), tag)

    def test_only_release_evidence_may_follow_included_through(self) -> None:
        for path in (
            "VERSION",
            "CHANGELOG.md",
            "release/config.yaml",
            "release/manifest.yaml",
            "release/migrations/v0.1.1.md",
        ):
            with self.subTest(path=path):
                self.assertTrue(
                    platform_release.is_release_evidence_path(path, "0.1.1")
                )
        for path in ("charts/studio/api/Chart.yaml", ".github/workflows/release.yaml"):
            with self.subTest(path=path):
                self.assertFalse(
                    platform_release.is_release_evidence_path(path, "0.1.1")
                )

    def test_release_date_requires_strict_calendar_format(self) -> None:
        platform_release.validate_release_date("2026-09-01")
        for value in ("2026-9-01", "2026-09-1", "2026-02-30", "2026/09/01"):
            with self.subTest(value=value):
                with self.assertRaises(platform_release.ReleaseError):
                    platform_release.validate_release_date(value)

    def test_prepare_rejects_invalid_and_duplicate_alpha_revisions(self) -> None:
        for value, message in (
            ("abc123", "full lowercase commits"),
            ("A" * 40, "full lowercase commits"),
            (f"{'a' * 40},{'a' * 40}", "contains duplicate commits"),
        ):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as directory:
                    version = Path(directory) / "VERSION"
                    version.write_text("0.1.0\n")
                    args = SimpleNamespace(
                        version="0.1.1",
                        previous_tag="v0.1.0",
                        release_date="2026-09-01",
                        stable_upgrade="supported",
                        upgrades_from_alpha_revisions=value,
                    )
                    with mock.patch.object(platform_release, "VERSION_PATH", version):
                        with self.assertRaisesRegex(
                            platform_release.ReleaseError, message
                        ):
                            platform_release.prepare_release(args)

    def test_workflow_actions_are_pinned_to_full_commits(self) -> None:
        for path in sorted((ROOT / ".github/workflows").glob("*.yaml")):
            workflow = path.read_text()
            uses = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, flags=re.MULTILINE)
            self.assertTrue(uses)
            for action in uses:
                with self.subTest(workflow=path.name, action=action):
                    self.assertRegex(action, r"@[0-9a-f]{40}$")

    def test_normal_validation_exposes_required_ci_context(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/validate.yaml").read_text())
        self.assertEqual(workflow["jobs"]["validate"]["name"], "Required CI")

    def test_client_source_update_preserves_signature_verification(self) -> None:
        original = """apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  annotations:
    platform.neurwerk.com/adoption-mode: fresh-install
    platform.neurwerk.com/adoption-target: v0.1.0
  name: k8s-stack
  namespace: flux-system
spec:
  interval: 30s
  url: https://github.com/neurwerk/k8s_stack_base.git
  ref:
    tag: v0.1.0
  verify:
    mode: Tag
    secretRef:
      name: k8s-stack-release-trust
"""
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "platform-source.yaml"
            candidate.write_text(original)
            platform_release.update_client_source(candidate, "v1.2.3")
            updated = candidate.read_text()
        expected = original.replace("tag: v0.1.0", "tag: v1.2.3")
        expected = expected.replace(
            "platform.neurwerk.com/adoption-target: v0.1.0",
            "platform.neurwerk.com/adoption-target: v1.2.3",
        )
        expected = expected.replace(
            "platform.neurwerk.com/adoption-mode: fresh-install",
            "platform.neurwerk.com/adoption-mode: review-required",
        )
        self.assertEqual(updated, expected)
        self.assertIn("mode: Tag", updated)
        self.assertIn("name: k8s-stack-release-trust", updated)
        self.assertIn("adoption-mode: review-required", updated)

        expanded = original.replace(
            "  verify:\n",
            "  include:\n"
            "    - repository:\n"
            "        name: flux-system\n"
            "  verify:\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "platform-source.yaml"
            candidate.write_text(expanded)
            with self.assertRaisesRegex(
                platform_release.ReleaseError, "canonical source spec"
            ):
                platform_release.update_client_source(candidate, "v1.2.3")

    def test_client_source_update_preserves_scalar_quotes_and_comments(self) -> None:
        original = """apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  annotations:
    platform.neurwerk.com/adoption-mode: 'upgrade' # reviewed target state
    platform.neurwerk.com/adoption-target: "v0.1.0" # current target
  name: k8s-stack
  namespace: flux-system
spec:
  interval: 30s
  url: https://github.com/neurwerk/k8s_stack_base.git
  ref:
    tag: 'v0.1.0' # current platform
  verify:
    mode: Tag
    secretRef:
      name: k8s-stack-release-trust
"""
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "platform-source.yaml"
            candidate.write_text(original)
            platform_release.update_client_source(candidate, "v1.2.3")
            updated = candidate.read_text()

        self.assertIn(
            "platform.neurwerk.com/adoption-mode: 'review-required' "
            "# reviewed target state",
            updated,
        )
        self.assertIn(
            'platform.neurwerk.com/adoption-target: "v1.2.3" # current target',
            updated,
        )
        self.assertIn("tag: 'v1.2.3' # current platform", updated)
        parsed = yaml.safe_load(updated)
        self.assertEqual(parsed["spec"]["ref"]["tag"], "v1.2.3")
        self.assertEqual(
            parsed["metadata"]["annotations"]["platform.neurwerk.com/adoption-mode"],
            "review-required",
        )

    def test_release_signer_accepts_the_exact_canonical_line(self) -> None:
        tag = self._release_integration_tag()
        key = (ROOT / "release/trust/platform-release.sshpub").read_text().split()
        allowed_signer = f'platform-release namespaces="git" {key[0]} {key[1]}'
        result = self._verify_release_tag(allowed_signer, tag)
        self.assertEqual(result.returncode, 0, result.stderr)

    def _verify_release_tag(
        self, allowed_signer: str, tag: str
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PLATFORM_RELEASE_ALLOWED_SIGNER"] = allowed_signer
        return subprocess.run(
            ["bash", "scripts/verify_release_tag.sh", tag],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
