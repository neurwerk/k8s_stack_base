"""Release manifest contract tests."""

from __future__ import annotations

import copy
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

    def test_release_evidence_is_consistent(self) -> None:
        platform_release.validate()

    def test_manifest_covers_every_chart(self) -> None:
        manifest = platform_release.build_manifest()
        recorded = {
            item["path"] for item in manifest["spec"]["artifacts"]["charts"]
        }
        actual = {
            path.parent.relative_to(ROOT).as_posix()
            for path in (ROOT / "charts").glob("**/Chart.yaml")
        }
        self.assertEqual(recorded, actual)

    def test_manifest_covers_every_helm_release(self) -> None:
        manifest = platform_release.build_manifest()
        recorded = {
            (item["namespace"], item["name"])
            for item in manifest["spec"]["artifacts"]["helmReleases"]
        }
        actual = {
            (item["namespace"], item["name"])
            for item in platform_release.helm_release_inventory()
        }
        self.assertEqual(recorded, actual)
        self.assertEqual(len(recorded), len(manifest["spec"]["artifacts"]["helmReleases"]))

    def test_default_packages_are_not_excluded(self) -> None:
        manifest = platform_release.build_manifest()
        packages = manifest["spec"]["packages"]
        excluded = {item["path"] for item in packages["optional"]}
        self.assertTrue(set(packages["default"]).isdisjoint(excluded))

    def test_manifest_matches_declared_schema(self) -> None:
        manifest = platform_release.build_manifest()
        platform_release.validate_manifest_schema(manifest)

    def test_release_schema_rejects_invalid_compatibility(self) -> None:
        manifest = platform_release.build_manifest()
        compatibility = manifest["spec"]["compatibility"]
        invalid_contracts: list[tuple[str, dict[str, object]]] = []

        missing = copy.deepcopy(compatibility)
        missing.pop("freshInstall")
        invalid_contracts.append(("missing field", missing))
        for name, field, value in (
            ("invalid fresh install", "freshInstall", "yes"),
            ("non-tag upgrade", "upgradesFrom", ["0.2.5"]),
            ("duplicate upgrades", "upgradesFrom", ["v0.1.0", "v0.1.0"]),
            ("supported downgrade", "downgrade", "supported"),
            ("unknown recovery", "recovery", "database-restore"),
        ):
            candidate = copy.deepcopy(compatibility)
            candidate[field] = value
            invalid_contracts.append((name, candidate))
        additional = copy.deepcopy(compatibility)
        additional["rollbackWindow"] = "24h"
        invalid_contracts.append(("additional property", additional))

        for name, candidate in invalid_contracts:
            with self.subTest(name=name):
                invalid_manifest = copy.deepcopy(manifest)
                invalid_manifest["spec"]["compatibility"] = candidate
                with self.assertRaises(platform_release.ReleaseError):
                    platform_release.validate_manifest_schema(invalid_manifest)

    def test_unpublished_baseline_has_no_migration(self) -> None:
        self.assertEqual(list((ROOT / "release/migrations").glob("v*.md")), [])
        self.assertEqual(
            platform_release.build_manifest()["spec"]["compatibility"],
            {
                "freshInstall": "supported",
                "upgradesFrom": [],
                "downgrade": "unsupported",
                "recovery": "replacement-restore",
            },
        )

    def test_migration_compatibility_parses_wrapped_source_versions(self) -> None:
        migration = """# Platform v0.1.2

## Support

- Fresh installation: Supported.
- Supported source versions: `v0.1.0`,
  `v0.1.1`.
- Downgrade: Unsupported.

## Recovery

Recovery classification: Replacement restore.
"""
        compatibility = platform_release.parse_migration_compatibility(migration)
        self.assertEqual(
            compatibility["upgradesFrom"],
            ["v0.1.0", "v0.1.1"],
        )

    def test_migration_compatibility_rejects_invalid_declarations(self) -> None:
        migration = """# Platform v0.1.2

## Support

- Fresh installation: Supported.
- Supported source versions: `v0.1.0`,
  `v0.1.1`.
- Downgrade: Unsupported.

## Recovery

Recovery classification: Replacement restore.
"""
        invalid_migrations = (
            (
                "missing fresh installation",
                migration.replace("- Fresh installation: Supported.\n", ""),
                "exactly one fresh installation declaration",
            ),
            (
                "duplicate fresh installation",
                migration.replace(
                    "- Fresh installation: Supported.",
                    "- Fresh installation: Supported.\n"
                    "- Fresh installation: Supported.",
                ),
                "exactly one fresh installation declaration",
            ),
            (
                "unknown fresh installation",
                migration.replace(
                    "- Fresh installation: Supported.",
                    "- Fresh installation: Conditional.",
                ),
                "unknown fresh installation value",
            ),
            (
                "misplaced fresh installation",
                migration.replace("- Fresh installation: Supported.\n", "").replace(
                    "Recovery classification: Replacement restore.",
                    "- Fresh installation: Supported.\n"
                    "Recovery classification: Replacement restore.",
                ),
                "fresh installation declaration must appear in ## Support",
            ),
            (
                "missing source versions",
                migration.replace(
                    "- Supported source versions: `v0.1.0`,\n  `v0.1.1`.\n", ""
                ),
                "exactly one supported source versions declaration",
            ),
            (
                "duplicate source versions",
                migration.replace(
                    "- Supported source versions: `v0.1.0`,",
                    "- Supported source versions: None.\n"
                    "- Supported source versions: `v0.1.0`,",
                ),
                "exactly one supported source versions declaration",
            ),
            (
                "malformed source versions",
                migration.replace("`v0.1.0`,", "v0.1.0,"),
                "must be None or comma-separated strict tags",
            ),
            (
                "duplicate source tags",
                migration.replace("`v0.1.1`.", "`v0.1.0`."),
                "source versions contain duplicate tags",
            ),
            (
                "invalid source continuation",
                migration.replace("  `v0.1.1`.", "`v0.1.1`."),
                "source versions declaration has invalid continuation",
            ),
            (
                "misplaced source versions",
                migration.replace(
                    "- Supported source versions: `v0.1.0`,\n  `v0.1.1`.\n", ""
                ).replace(
                    "Recovery classification: Replacement restore.",
                    "- Supported source versions: None.\n"
                    "Recovery classification: Replacement restore.",
                ),
                "source versions declaration must appear in ## Support",
            ),
            (
                "missing downgrade",
                migration.replace("- Downgrade: Unsupported.\n", ""),
                "exactly one downgrade declaration",
            ),
            (
                "duplicate downgrade",
                migration.replace(
                    "- Downgrade: Unsupported.",
                    "- Downgrade: Unsupported.\n- Downgrade: Unsupported.",
                ),
                "exactly one downgrade declaration",
            ),
            (
                "unknown downgrade",
                migration.replace(
                    "- Downgrade: Unsupported.", "- Downgrade: Conditional."
                ),
                "unknown downgrade value",
            ),
            (
                "misplaced downgrade",
                migration.replace(
                    "- Downgrade: Unsupported.\n\n## Recovery",
                    "## Recovery\n\n- Downgrade: Unsupported.",
                ),
                "downgrade declaration must appear in ## Support",
            ),
            (
                "malformed downgrade",
                migration.replace(
                    "- Downgrade: Unsupported.", "- Downgrade: unsupported."
                ),
                "downgrade declaration has invalid format",
            ),
            (
                "missing recovery",
                migration.replace("Recovery classification: Replacement restore.\n", ""),
                "exactly one recovery classification declaration",
            ),
            (
                "duplicate recovery",
                migration.replace(
                    "Recovery classification: Replacement restore.",
                    "Recovery classification: Replacement restore.\n"
                    "Recovery classification: Replacement restore.",
                ),
                "exactly one recovery classification declaration",
            ),
            (
                "unknown recovery",
                migration.replace("Replacement restore", "Database restore"),
                "unknown recovery classification",
            ),
            (
                "misplaced recovery",
                migration.replace(
                    "Recovery classification: Replacement restore.\n", ""
                ).replace(
                    "- Downgrade: Unsupported.",
                    "- Downgrade: Unsupported.\n"
                    "Recovery classification: Replacement restore.",
                ),
                "recovery classification declaration must appear in ## Recovery",
            ),
        )
        for name, candidate, message in invalid_migrations:
            with self.subTest(name=name):
                with self.assertRaisesRegex(platform_release.ReleaseError, message):
                    platform_release.parse_migration_compatibility(candidate)

    def test_migration_compatibility_rejects_contract_mismatch(self) -> None:
        migration = """## Support

- Fresh installation: Supported.
- Supported source versions: `v0.1.0`, `v0.1.1`.
- Downgrade: Unsupported.

## Recovery

Recovery classification: Forward fix.
"""
        matching = {
            "freshInstall": "supported",
            "upgradesFrom": ["v0.1.0", "v0.1.1"],
            "downgrade": "unsupported",
            "recovery": "forward-fix",
        }
        mismatches = (
            (
                "freshInstall",
                {**matching, "freshInstall": "unsupported"},
                "migration freshInstall does not match release config compatibility.freshInstall",
            ),
            (
                "upgradesFrom set",
                {**matching, "upgradesFrom": ["v0.0.9", "v0.1.0"]},
                "migration upgradesFrom set does not match release config compatibility.upgradesFrom",
            ),
            (
                "upgradesFrom order",
                {**matching, "upgradesFrom": ["v0.1.1", "v0.1.0"]},
                "migration upgradesFrom order does not match release config compatibility.upgradesFrom",
            ),
            (
                "downgrade",
                {**matching, "downgrade": "supported"},
                "migration downgrade does not match release config compatibility.downgrade",
            ),
            (
                "recovery",
                {**matching, "recovery": "replacement-restore"},
                "migration recovery does not match release config compatibility.recovery",
            ),
        )
        for name, compatibility, message in mismatches:
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    platform_release.ReleaseError,
                    re.escape(message),
                ):
                    platform_release.validate_migration_compatibility(
                        migration, compatibility, "release config compatibility"
                    )

    def test_final_validation_uses_migration_compatibility(self) -> None:
        config = {
            "releaseDate": "2026-09-01",
            "summary": "Reviewed release.",
            "compatibility": {
                "freshInstall": "supported",
                "upgradesFrom": [],
                "downgrade": "unsupported",
                "recovery": "replacement-restore",
            },
        }
        mismatch = platform_release.ReleaseError(
            "release migration recovery does not match release config compatibility.recovery"
        )
        errors: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "release/migrations").mkdir(parents=True)
            (root / "release/migrations/v0.1.0.md").write_text("Reviewed migration.\n")
            changelog = root / "CHANGELOG.md"
            changelog.write_text(
                "# Changelog\n\n## [0.1.0] - 2026-09-01\n\nReviewed release.\n"
            )
            with (
                mock.patch.object(platform_release, "ROOT", root),
                mock.patch.object(platform_release, "CHANGELOG_PATH", changelog),
                mock.patch.object(
                    platform_release,
                    "validate_migration_compatibility",
                    side_effect=mismatch,
                ) as validate_compatibility,
            ):
                platform_release.validate_release_prose(config, "0.1.0", errors)
        validate_compatibility.assert_called_once()
        self.assertIn(str(mismatch), errors)

    def test_publication_rejects_config_manifest_compatibility_mismatch(self) -> None:
        compatibility = {
            "freshInstall": "supported",
            "upgradesFrom": [],
            "downgrade": "unsupported",
            "recovery": "replacement-restore",
        }
        config = {
            "version": "0.1.1",
            "releaseDate": "2026-09-01",
            "compatibility": compatibility,
        }
        manifest = {
            "metadata": {"name": "v0.1.1"},
            "spec": {
                "version": "0.1.1",
                "releaseDate": "2026-09-01",
                "compatibility": {**compatibility, "recovery": "forward-fix"},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "release").mkdir()
            (root / "VERSION").write_text("0.1.1\n")
            (root / "release/config.yaml").write_text("{}\n")
            (root / "release/manifest.yaml").write_text("{}\n")
            with (
                mock.patch.object(
                    platform_release, "load_yaml", side_effect=[config, manifest]
                ),
                mock.patch.object(platform_release, "validate_manifest_schema"),
            ):
                with self.assertRaisesRegex(
                    platform_release.ReleaseError,
                    "config and manifest compatibility do not match",
                ):
                    platform_release.inspect_release_data(root, "v0.1.1")

    def test_release_provenance_matches_exact_git_history(self) -> None:
        self._release_integration_tag()
        expected = platform_release.build_manifest()["spec"].get("provenance")
        if expected is None:
            self.skipTest("bootstrap release has no predecessor provenance")
        self.assertEqual(
            platform_release.provenance_from_git(
                expected["previousTag"], expected["includedThrough"]
            ),
            expected,
        )

    def test_release_provenance_rejects_divergent_history(self) -> None:
        with (
            mock.patch.object(
                platform_release,
                "git",
                side_effect=["a" * 40, "b" * 40],
            ),
            mock.patch.object(platform_release, "git_is_ancestor", return_value=False),
        ):
            with self.assertRaisesRegex(platform_release.ReleaseError, "not an ancestor"):
                platform_release.provenance_from_git("v0.1.0", "b" * 40)

    def test_bootstrap_provenance_records_complete_history(self) -> None:
        included = "b" * 40
        commits = ["a" * 40, included]

        def git_result(*arguments: str, repository: Path = ROOT) -> str:
            self.assertEqual(repository, ROOT)
            if arguments == ("rev-parse", "HEAD"):
                return included
            if arguments == ("rev-list", "--reverse", included):
                return "\n".join(commits)
            self.fail(f"unexpected git arguments: {arguments}")

        with mock.patch.object(platform_release, "git", side_effect=git_result):
            provenance = platform_release.bootstrap_provenance_from_git()

        self.assertEqual(
            provenance,
            {
                "bootstrap": True,
                "includedThrough": included,
                "commits": commits,
                "historyUrl": (
                    "https://github.com/neurwerk/k8s_stack_base/commits/"
                    f"{included}"
                ),
            },
        )

    def test_previous_tag_must_match_version_at_included_through(self) -> None:
        with mock.patch.object(platform_release, "git", return_value="0.1.0"):
            platform_release.validate_previous_tag_at_included_through(
                "v0.1.0", "a" * 40
            )
            with self.assertRaisesRegex(
                platform_release.ReleaseError,
                "previousTag does not match VERSION at includedThrough",
            ):
                platform_release.validate_previous_tag_at_included_through(
                    "v0.0.9", "a" * 40
                )

    def test_final_validation_checks_version_at_included_through(self) -> None:
        provenance = {
            "previousTag": "v0.1.0",
            "includedThrough": "a" * 40,
            "commits": ["a" * 40],
            "compareUrl": (
                "https://github.com/neurwerk/k8s_stack_base/compare/v0.1.0..."
                f"{'a' * 40}"
            ),
        }
        errors: list[str] = []
        mismatch = platform_release.ReleaseError(
            "release provenance previousTag does not match VERSION at includedThrough"
        )
        with (
            mock.patch.object(
                platform_release, "provenance_from_git", return_value=provenance
            ),
            mock.patch.object(
                platform_release,
                "validate_previous_tag_at_included_through",
                side_effect=mismatch,
            ) as validate_previous,
            mock.patch.object(platform_release, "git", return_value=""),
            mock.patch.object(platform_release, "git_is_ancestor", return_value=True),
        ):
            platform_release.validate_provenance(
                {"provenance": provenance}, "0.1.1", errors
            )
        validate_previous.assert_called_once_with("v0.1.0", "a" * 40)
        self.assertIn(str(mismatch), errors)

    def test_publication_inspection_checks_version_at_included_through(self) -> None:
        provenance = {
            "previousTag": "v0.1.0",
            "includedThrough": "a" * 40,
            "commits": ["a" * 40],
            "compareUrl": (
                "https://github.com/neurwerk/k8s_stack_base/compare/v0.1.0..."
                f"{'a' * 40}"
            ),
        }
        config = {
            "version": "0.1.1",
            "releaseDate": "2026-09-01",
            "provenance": provenance,
        }
        manifest = {
            "metadata": {"name": "v0.1.1"},
            "spec": {
                "version": "0.1.1",
                "releaseDate": "2026-09-01",
                "provenance": provenance,
            },
        }
        mismatch = platform_release.ReleaseError(
            "release provenance previousTag does not match VERSION at includedThrough"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "release").mkdir()
            (root / "VERSION").write_text("0.1.1\n")
            (root / "release/config.yaml").write_text("{}\n")
            (root / "release/manifest.yaml").write_text("{}\n")
            with (
                mock.patch.object(
                    platform_release, "load_yaml", side_effect=[config, manifest]
                ),
                mock.patch.object(platform_release, "validate_manifest_schema"),
                mock.patch.object(platform_release, "git", return_value="b" * 40),
                mock.patch.object(
                    platform_release, "git_is_ancestor", return_value=True
                ),
                mock.patch.object(
                    platform_release,
                    "validate_previous_tag_at_included_through",
                    side_effect=mismatch,
                ) as validate_previous,
            ):
                with self.assertRaisesRegex(
                    platform_release.ReleaseError,
                    "previousTag does not match VERSION at includedThrough",
                ):
                    platform_release.inspect_release_data(root, "v0.1.1")
        validate_previous.assert_called_once_with(
            "v0.1.0", "a" * 40, repository=root.resolve()
        )

    def test_publication_inspects_bootstrap_complete_history(self) -> None:
        included = "a" * 40
        tag_commit = "b" * 40
        compatibility = {
            "freshInstall": "supported",
            "upgradesFrom": [],
            "downgrade": "unsupported",
            "recovery": "replacement-restore",
        }
        provenance = {
            "bootstrap": True,
            "includedThrough": included,
            "commits": [included],
            "historyUrl": (
                "https://github.com/neurwerk/k8s_stack_base/commits/" f"{included}"
            ),
        }
        config = {
            "version": "0.1.0",
            "releaseDate": "2026-09-01",
            "compatibility": compatibility,
            "provenance": provenance,
        }
        manifest = {
            "metadata": {"name": "v0.1.0"},
            "spec": {
                "version": "0.1.0",
                "releaseDate": "2026-09-01",
                "compatibility": compatibility,
                "provenance": provenance,
            },
        }

        def git_result(*arguments: str, repository: Path = ROOT) -> str:
            if arguments == ("rev-parse", "--verify", "v0.1.0^{commit}"):
                return tag_commit
            if arguments == (
                "diff",
                "--name-only",
                f"{included}..{tag_commit}",
            ):
                return ""
            self.fail(f"unexpected git arguments: {arguments}")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "release/migrations").mkdir(parents=True)
            (root / "VERSION").write_text("0.1.0\n")
            (root / "release/config.yaml").write_text("{}\n")
            (root / "release/manifest.yaml").write_text("{}\n")
            (root / "CHANGELOG.md").write_text(
                "# Changelog\n\n## [0.1.0] - 2026-09-01\n\nReviewed release.\n"
            )
            (root / "release/migrations/v0.1.0.md").write_text(
                "# Platform v0.1.0\n\n"
                "## Support\n\n"
                "- Fresh installation: Supported.\n"
                "- Supported source versions: None.\n"
                "- Downgrade: Unsupported.\n\n"
                "## Recovery\n\n"
                "Recovery classification: Replacement restore.\n\n"
                "Use the reviewed replacement procedure.\n"
            )
            with (
                mock.patch.object(
                    platform_release, "load_yaml", side_effect=[config, manifest]
                ),
                mock.patch.object(platform_release, "validate_manifest_schema"),
                mock.patch.object(platform_release, "git", side_effect=git_result),
                mock.patch.object(
                    platform_release, "git_is_ancestor", return_value=True
                ),
                mock.patch.object(
                    platform_release,
                    "bootstrap_provenance_from_git",
                    return_value=provenance,
                ),
                mock.patch.object(
                    platform_release, "repository_tags", return_value=["v0.1.0"]
                ),
            ):
                inspected = platform_release.inspect_release_data(root, "v0.1.0")

        self.assertEqual(inspected, provenance)

    def test_current_commit_resolves_to_one_release_tag(self) -> None:
        tag = self._release_integration_tag()
        commit = platform_release.git("rev-parse", f"{tag}^{{commit}}")
        self.assertEqual(platform_release.release_tag_for_commit(commit), tag)

    def test_release_schema_rejects_incomplete_provenance(self) -> None:
        manifest = copy.deepcopy(platform_release.build_manifest())
        manifest["spec"]["version"] = "0.1.1"
        manifest["spec"]["releaseDate"] = "2026-09-01"
        manifest["spec"]["provenance"] = {
            "previousTag": "v0.1.0",
            "includedThrough": "f2289fc9e668d6de7b5738ed825378874b9540d4",
            "compareUrl": "https://github.com/neurwerk/k8s_stack_base/compare/v0.1.0...f2289fc9e668d6de7b5738ed825378874b9540d4",
        }
        with self.assertRaises(platform_release.ReleaseError):
            platform_release.validate_manifest_schema(manifest)

    def test_release_schema_requires_provenance_after_bootstrap(self) -> None:
        manifest = copy.deepcopy(platform_release.build_manifest())
        manifest["spec"]["version"] = "0.1.1"
        manifest["spec"]["releaseDate"] = "2026-09-01"
        with self.assertRaises(platform_release.ReleaseError):
            platform_release.validate_manifest_schema(manifest)

    def test_release_schema_reserves_bootstrap_for_exact_v0_1_0(self) -> None:
        manifest = copy.deepcopy(platform_release.build_manifest())
        included = "a" * 40
        bootstrap = {
            "bootstrap": True,
            "includedThrough": included,
            "commits": [included],
            "historyUrl": (
                "https://github.com/neurwerk/k8s_stack_base/commits/" f"{included}"
            ),
        }
        predecessor = {
            "previousTag": "v0.1.0",
            "includedThrough": included,
            "commits": [included],
            "compareUrl": (
                "https://github.com/neurwerk/k8s_stack_base/compare/v0.1.0..."
                f"{included}"
            ),
        }

        manifest["spec"]["version"] = "0.1.0"
        manifest["spec"]["releaseDate"] = "2026-09-01"
        manifest["spec"]["provenance"] = bootstrap
        platform_release.validate_manifest_schema(manifest)

        manifest["spec"]["provenance"] = predecessor
        with self.assertRaises(platform_release.ReleaseError):
            platform_release.validate_manifest_schema(manifest)

        manifest["spec"]["version"] = "0.1.1"
        manifest["spec"]["provenance"] = bootstrap
        with self.assertRaises(platform_release.ReleaseError):
            platform_release.validate_manifest_schema(manifest)

    def test_unpublished_baseline_rejects_release_fields(self) -> None:
        manifest = copy.deepcopy(platform_release.build_manifest())
        manifest["spec"]["releaseDate"] = "2026-09-01"
        with self.assertRaises(platform_release.ReleaseError):
            platform_release.validate_manifest_schema(manifest)

    def test_unpublished_baseline_cannot_be_tagged(self) -> None:
        with self.assertRaisesRegex(
            platform_release.ReleaseError,
            "unpublished pre-v0.1.0 history cannot be released",
        ):
            platform_release.validate("v0.0.0")

    def test_release_scaffolds_remain_invalid_until_curated(self) -> None:
        self.assertTrue(platform_release.contains_todo("TODO: write migration evidence"))
        self.assertFalse(platform_release.contains_todo("No operator action is required."))

    def test_only_release_evidence_may_follow_included_through(self) -> None:
        for path in (
            "VERSION",
            "CHANGELOG.md",
            "release/config.yaml",
            "release/manifest.yaml",
            "release/migrations/v0.1.1.md",
        ):
            with self.subTest(path=path):
                self.assertTrue(platform_release.is_release_evidence_path(path, "0.1.1"))
        for path in ("charts/studio/api/Chart.yaml", ".github/workflows/release.yaml"):
            with self.subTest(path=path):
                self.assertFalse(platform_release.is_release_evidence_path(path, "0.1.1"))

    def test_bootstrap_preparation_requires_zero_tags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            version = Path(directory) / "VERSION"
            version.write_text("0.0.0\n")
            args = SimpleNamespace(
                bootstrap=True,
                version="0.1.0",
                release_date="2026-09-01",
                upgrades_from="",
                fresh_install="supported",
            )
            with (
                mock.patch.object(platform_release, "VERSION_PATH", version),
                mock.patch.object(
                    platform_release, "repository_tags", return_value=["legacy"]
                ),
            ):
                with self.assertRaisesRegex(
                    platform_release.ReleaseError, "requires a repository with zero tags"
                ):
                    platform_release.prepare_release(args)

    def test_bootstrap_preparation_writes_complete_history_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release_dir = root / "release"
            (release_dir / "migrations").mkdir(parents=True)
            config = release_dir / "config.yaml"
            manifest = release_dir / "manifest.yaml"
            version = root / "VERSION"
            changelog = root / "CHANGELOG.md"
            config.write_text("version: 0.0.0\npackages: {}\n")
            version.write_text("0.0.0\n")
            changelog.write_text("# Changelog\n\n## [Unreleased]\n")
            included = "a" * 40
            provenance = {
                "bootstrap": True,
                "includedThrough": included,
                "commits": [included],
                "historyUrl": (
                    "https://github.com/neurwerk/k8s_stack_base/commits/"
                    f"{included}"
                ),
            }
            args = SimpleNamespace(
                bootstrap=True,
                version="0.1.0",
                previous_tag=None,
                release_date="2026-09-01",
                summary="Establish the first reviewed platform release.",
                fresh_install="supported",
                upgrades_from="",
                recovery="replacement-restore",
            )
            with (
                mock.patch.object(platform_release, "ROOT", root),
                mock.patch.object(platform_release, "CONFIG_PATH", config),
                mock.patch.object(platform_release, "MANIFEST_PATH", manifest),
                mock.patch.object(platform_release, "VERSION_PATH", version),
                mock.patch.object(platform_release, "CHANGELOG_PATH", changelog),
                mock.patch.object(platform_release, "repository_tags", return_value=[]),
                mock.patch.object(
                    platform_release,
                    "bootstrap_provenance_from_git",
                    return_value=provenance,
                ),
                mock.patch.object(
                    platform_release,
                    "verify_release_tag_signature",
                ) as verify_signature,
                mock.patch.object(
                    platform_release,
                    "build_manifest",
                    return_value={"spec": {"provenance": provenance}},
                ),
                mock.patch.object(platform_release, "validate_manifest_schema"),
            ):
                platform_release.prepare_release(args)

            verify_signature.assert_not_called()
            prepared = yaml.safe_load(config.read_text())
            self.assertEqual(prepared["provenance"], provenance)
            self.assertEqual(
                prepared["compatibility"],
                {
                    "freshInstall": "supported",
                    "upgradesFrom": [],
                    "downgrade": "unsupported",
                    "recovery": "replacement-restore",
                },
            )
            self.assertEqual(version.read_text(), "0.1.0\n")

    def test_release_preparation_writes_only_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release_dir = root / "release"
            migration_dir = release_dir / "migrations"
            migration_dir.mkdir(parents=True)
            config = release_dir / "config.yaml"
            manifest = release_dir / "manifest.yaml"
            version = root / "VERSION"
            changelog = root / "CHANGELOG.md"
            config.write_text("version: 0.1.0\npackages: {}\n")
            version.write_text("0.1.0\n")
            changelog.write_text("# Changelog\n\n## [Unreleased]\n")
            provenance = {
                "previousTag": "v0.1.0",
                "includedThrough": "a" * 40,
                "commits": ["a" * 40],
                "compareUrl": (
                    "https://github.com/neurwerk/k8s_stack_base/compare/v0.1.0..."
                    f"{'a' * 40}"
                ),
            }
            args = SimpleNamespace(
                version="0.1.1",
                previous_tag="v0.1.0",
                release_date="2026-09-01",
                summary="Prepare the next reviewed platform release.",
                fresh_install="supported",
                upgrades_from="",
                recovery="replacement-restore",
            )
            with (
                mock.patch.object(platform_release, "ROOT", root),
                mock.patch.object(platform_release, "CONFIG_PATH", config),
                mock.patch.object(platform_release, "MANIFEST_PATH", manifest),
                mock.patch.object(platform_release, "VERSION_PATH", version),
                mock.patch.object(platform_release, "CHANGELOG_PATH", changelog),
                mock.patch.object(
                    platform_release, "latest_release_tag", return_value="v0.1.0"
                ),
                mock.patch.object(
                    platform_release, "verify_release_tag_signature"
                ) as verify_signature,
                mock.patch.object(
                    platform_release, "provenance_from_git", return_value=provenance
                ),
                mock.patch.object(
                    platform_release,
                    "build_manifest",
                    return_value={"spec": {"provenance": provenance}},
                ),
                mock.patch.object(platform_release, "validate_manifest_schema"),
            ):
                platform_release.prepare_release(args)
            verify_signature.assert_called_once_with("v0.1.0", "v0.1.0")

            prepared = yaml.safe_load(config.read_text())
            self.assertEqual(prepared["provenance"], provenance)
            self.assertEqual(prepared["compatibility"]["downgrade"], "unsupported")
            self.assertEqual(version.read_text(), "0.1.1\n")
            self.assertTrue(platform_release.contains_todo(changelog.read_text()))
            self.assertTrue(
                platform_release.contains_todo(
                    (migration_dir / "v0.1.1.md").read_text()
                )
            )
            self.assertTrue(manifest.is_file())
            prose_errors: list[str] = []
            with (
                mock.patch.object(platform_release, "ROOT", root),
                mock.patch.object(platform_release, "CHANGELOG_PATH", changelog),
            ):
                platform_release.validate_release_prose(prepared, "0.1.1", prose_errors)
            self.assertIn("release changelog section contains TODO markers", prose_errors)
            self.assertIn("release migration document contains TODO markers", prose_errors)
            self.assertEqual(
                {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()},
                {
                    "CHANGELOG.md",
                    "VERSION",
                    "release/config.yaml",
                    "release/manifest.yaml",
                    "release/migrations/v0.1.1.md",
                },
            )

    def test_release_date_requires_strict_calendar_format(self) -> None:
        platform_release.validate_release_date("2026-09-01")
        for value in ("2026-9-01", "2026-09-1", "2026-02-30", "2026/09/01"):
            with self.subTest(value=value):
                with self.assertRaises(platform_release.ReleaseError):
                    platform_release.validate_release_date(value)

    def test_release_preparation_requires_current_signed_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            version = Path(directory) / "VERSION"
            version.write_text("0.1.0\n")
            args = SimpleNamespace(
                version="0.1.1",
                previous_tag="v0.0.9",
                release_date="2026-09-01",
                upgrades_from="",
            )
            with mock.patch.object(platform_release, "VERSION_PATH", version):
                with self.assertRaisesRegex(
                    platform_release.ReleaseError, "expected v0.1.0"
                ):
                    platform_release.prepare_release(args)

    def test_legacy_release_adoption_has_actionable_minimum_version(self) -> None:
        with self.assertRaisesRegex(
            platform_release.ReleaseError,
            "client adoption requires v0.1.0 or newer",
        ):
            platform_release.inspect_release_data(ROOT, "v0.0.0")

    def test_release_versions_are_strict_semver(self) -> None:
        for value in ("v1.2.3", "v0.1.0"):
            with self.subTest(value=value):
                self.assertIsNotNone(platform_release.RELEASE_TAG.fullmatch(value))
        for value in ("v1.2", "v01.2.3", "v1.2.3-rc.1", "1.2.3"):
            with self.subTest(value=value):
                self.assertIsNone(platform_release.RELEASE_TAG.fullmatch(value))

    def test_manifest_covers_every_rendered_image(self) -> None:
        manifest = platform_release.build_manifest()
        recorded = {
            item["reference"] for item in manifest["spec"]["artifacts"]["images"]
        }
        self.assertTrue(set(platform_release.rendered_image_inventory()).issubset(recorded))
        self.assertIn(
            "registry.librechat.ai/danny-avila/librechat-rag-api-dev-lite:v0.9.0",
            recorded,
        )
        self.assertIn(
            "registry.librechat.ai/danny-avila/librechat-rag-api-dev:v0.9.0",
            recorded,
        )

    def test_release_publication_uses_external_signer_trust(self) -> None:
        verify = (ROOT / ".github/workflows/release.yaml").read_text()
        publish = (ROOT / ".github/workflows/publish-release.yaml").read_text()
        self.assertIn("name: Verify Platform Release", verify)
        self.assertIn("contents: read", verify)
        self.assertNotIn("contents: write", verify)
        self.assertIn("Checkout trusted release tooling", verify)
        self.assertIn("../release-tooling/scripts/verify_release_tag.sh", verify)
        self.assertIn("workflow_run:", publish)
        self.assertIn("environment: platform-release", publish)
        self.assertIn("contents: write", publish)
        self.assertIn("inspect-release", publish)
        self.assertIn("group: platform-release-publication", publish)
        self.assertIn("bootstrap publication requires v0.1.0", publish)
        self.assertIn("requires zero existing GitHub Releases", publish)
        self.assertIn("repository's only tag", publish)
        self.assertIn('previous_tag_name="$PREVIOUS_TAG"', publish)
        self.assertIn("git merge-base --is-ancestor", publish)
        self.assertIn(
            "cmp ../release-tooling/.github/workflows/release.yaml", publish
        )

    def test_release_preparation_is_draft_only_and_release_evidence_only(self) -> None:
        workflow = (ROOT / ".github/workflows/prepare-release-pr.yaml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("push:\n", workflow)
        self.assertIn("draft: always-true", workflow)
        self.assertIn("release/manifest.yaml", workflow)
        self.assertIn("secrets.RELEASE_AUTOMATION_APP_ID", workflow)
        self.assertIn("token: ${{ steps.app-token.outputs.token }}", workflow)
        self.assertIn('gh pr edit "$PR_NUMBER" --add-label "release: platform"', workflow)
        self.assertIn("continue-on-error: true", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("permission-contents: write", workflow)
        self.assertIn("bootstrap-v0.1.0", workflow)
        self.assertIn('mode_args=(--bootstrap)', workflow)
        self.assertIn('mode_args=(--previous-tag "$PREVIOUS_TAG")', workflow)
        self.assertNotIn("${{ inputs.downgrade }}", workflow)
        self.assertNotIn("--downgrade", workflow)
        self.assertNotIn("git tag", workflow)
        self.assertNotIn("kubectl", workflow)
        self.assertNotIn("flux reconcile", workflow)

    def test_client_adoption_is_opt_in_draft_only_and_reverifies_tag(self) -> None:
        workflow = (ROOT / ".github/workflows/client-adoption.yaml").read_text()
        self.assertIn("workflow_run:", workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)
        self.assertIn("vars.CLIENT_ADOPTION_ENABLED == 'true'", workflow)
        self.assertIn("fromJSON(vars.CLIENT_ADOPTION_REPOSITORIES", workflow)
        self.assertIn("secrets.CLIENT_ADOPTION_APP_ID", workflow)
        self.assertIn("../release-tooling/scripts/verify_release_tag.sh", workflow)
        self.assertIn("Checkout trusted default-branch tooling", workflow)
        self.assertIn("Checkout release tag as data", workflow)
        self.assertIn("inspect-release", workflow)
        self.assertIn("repositories: ${{ matrix.target.name }}", workflow)
        self.assertIn("permission-contents: write", workflow)
        self.assertIn("permission-pull-requests: write", workflow)
        self.assertIn("draft: always-true", workflow)
        self.assertNotIn("gh pr merge", workflow)

    def test_client_adoption_variable_scope_is_documented(self) -> None:
        readme = (ROOT / "README.md").read_text()
        self.assertIn(
            "Set `CLIENT_ADOPTION_ENABLED` and `CLIENT_ADOPTION_REPOSITORIES` as repository or",
            readme,
        )
        self.assertIn("must not be environment variables", readme)
        self.assertIn("only a reviewer gate and secret boundary", readme)

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

    def test_issue_forms_and_release_note_configuration_are_valid_yaml(self) -> None:
        issue_dir = ROOT / ".github/ISSUE_TEMPLATE"
        for path in sorted(issue_dir.glob("*.yml")):
            with self.subTest(path=path.name):
                self.assertIsInstance(yaml.safe_load(path.read_text()), dict)
        release_config = yaml.safe_load((ROOT / ".github/release.yml").read_text())
        titles = [item["title"] for item in release_config["changelog"]["categories"]]
        self.assertEqual(titles[-1], "Other Changes")
        self.assertEqual(
            release_config["changelog"]["exclude"]["labels"],
            ["release: none", "skip-changelog"],
        )
        proposal = yaml.safe_load(
            (issue_dir / "04-release-proposal.yml").read_text()
        )
        self.assertIn("release: platform", proposal["labels"])
        provenance = next(item for item in proposal["body"] if item.get("id") == "provenance")
        self.assertIn("One-time v0.1.0 bootstrap", provenance["attributes"]["options"])
        previous = next(item for item in proposal["body"] if item.get("id") == "previous")
        self.assertNotIn("validations", previous)

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

    def test_release_verifier_constructs_only_the_canonical_trust_line(self) -> None:
        verifier = (ROOT / "scripts/verify_release_tag.sh").read_text()
        canonical = "printf 'platform-release namespaces=\"git\" ssh-ed25519 %s\\n'"
        self.assertIn(canonical, verifier)
        self.assertIn("*$'\\n'*", verifier)
        self.assertIn("*$'\\r'*", verifier)
        self.assertNotIn('printf \'%s\\n\' "$allowed_signer"', verifier)

    def test_release_signer_rejects_additional_data(self) -> None:
        key = (ROOT / "release/trust/platform-release.sshpub").read_text().split()
        valid = f'platform-release namespaces="git" {key[0]} {key[1]}'
        invalid_values = (
            f"{valid} trailing-field",
            f"{valid}\nattacker namespaces=\"git\" {key[0]} {key[1]}",
            f"{valid}\rattacker namespaces=\"git\" {key[0]} {key[1]}",
        )
        for value in invalid_values:
            with self.subTest(value=repr(value)):
                result = self._verify_release_tag(value)
                self.assertNotEqual(result.returncode, 0)

    def test_release_signer_accepts_the_exact_canonical_line(self) -> None:
        tag = self._release_integration_tag()
        key = (ROOT / "release/trust/platform-release.sshpub").read_text().split()
        allowed_signer = f'platform-release namespaces="git" {key[0]} {key[1]}'
        result = self._verify_release_tag(allowed_signer, tag)
        self.assertEqual(result.returncode, 0, result.stderr)

    def _verify_release_tag(
        self, allowed_signer: str, tag: str = "v1.2.3"
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
