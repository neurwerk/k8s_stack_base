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
    def test_keycloak_branding_render_contract(self) -> None:
        def render(auth):
            return subprocess.run(
                ["helm", "template", "keycloak", str(ROOT / "charts/keycloak/server"),
                 "--values", str(ROOT / "tests/validation/helm-lint-values.yaml"),
                 "--values", "-"],
                input=yaml.safe_dump({"authKeycloak": auth}),
                capture_output=True, text=True, check=False,
            )

        result = render({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("KC_REALM_LOGIN_THEME", result.stdout)
        self.assertNotIn("KC_REALM_EMAIL_THEME", result.stdout)
        self.assertNotIn("name: client-brand", result.stdout)
        self.assertNotIn("name: auth-keycloak-branding-properties", result.stdout)
        self.assertNotIn("checksum/branding", result.stdout)

        auth = {
            "branding": {"enabled": True, "logoConfigMapName": "keycloak-branding-logo"},
            "realmDisplayName": " Example Company \\ =:#!\t\f\u00e9",
            "loginTheme": "client-brand", "emailTheme": "client-brand",
        }
        result = render(auth)
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = {doc["kind"]: doc for doc in yaml.safe_load_all(result.stdout) if doc}
        properties = docs["ConfigMap"]["data"]
        expected = "parent=neurwerk\ncompanyName=\\ Example\\ Company\\ \\\\\\ \\=\\:\\#\\!\\t\\f\u00e9\n"
        self.assertEqual(properties, {
            "login-theme.properties": expected, "email-theme.properties": expected,
        })
        sts = docs["StatefulSet"]
        self.assertEqual(sts["metadata"]["annotations"]["configmap.reloader.stakater.com/reload"],
                         "keycloak-branding-logo")
        pod = sts["spec"]["template"]
        checksum = pod["metadata"]["annotations"]["checksum/branding"]
        volume = next(v for v in pod["spec"]["volumes"] if v["name"] == "client-brand")
        self.assertEqual(volume["projected"]["defaultMode"], 0o444)
        self.assertEqual(volume["projected"]["sources"], [
            {"configMap": {"name": "auth-keycloak-branding-properties", "items": [
                {"key": "login-theme.properties", "path": "login/theme.properties"},
                {"key": "email-theme.properties", "path": "email/theme.properties"}]}},
            {"configMap": {"name": "keycloak-branding-logo", "items": [
                {"key": "company-logo.png", "path": "login/resources/img/company-logo.png"}]}},
        ])
        self.assertIn({"name": "client-brand", "mountPath": "/opt/keycloak/themes/client-brand",
                       "readOnly": True}, pod["spec"]["containers"][0]["volumeMounts"])
        env = docs["Job"]["spec"]["template"]["spec"]["containers"][0]["env"]
        for name in ("KC_REALM_LOGIN_THEME", "KC_REALM_EMAIL_THEME"):
            self.assertIn({"name": name, "value": "client-brand"}, env)
        auth["realmDisplayName"] = "Example Company"
        self.assertNotIn(checksum, render(auth).stdout)
        auth["activeDirectory"] = {
            "enabled": True, "connectionUrl": "ldaps://ad.example:636",
            "usersDn": "OU=Users,DC=example", "groupsDn": "OU=Groups,DC=example",
            "groupNames": ["neurwerk-platform-admins"], "egressCidrs": ["192.0.2.1/32"],
        }
        result = render(auth)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"auth-keycloak-active-directory-ca,keycloak-branding-logo"', result.stdout)
        for key in ("loginTheme", "emailTheme"):
            result = render({key: "client-brand"})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("client-brand selection requires", result.stderr)
        for name in ("line\nbreak", "line\rbreak", "${env.NAME}", "\\${name}"):
            with self.subTest(company=name):
                result = render({**auth, "realmDisplayName": name})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must not contain CR, LF, or property interpolation", result.stderr)
        for name in ("", "../logo", "Logo", "logo,other", "x" * 64):
            with self.subTest(configmap=name):
                result = render({**auth, "branding": {"enabled": True, "logoConfigMapName": name}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("authKeycloak.branding.logoConfigMapName", result.stderr)

    def test_compact_notes_preserve_only_selected_authored_body(self) -> None:
        body = (
            "- Fix LibreChat MCP authentication with internal routing.\n"
            "- Preserve required network access in public-DNS mode."
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("0.3.2\n")
            changelog = root / "CHANGELOG.md"
            for instructions in (
                "",
                "\n\n### Special Instructions\n\nReview the routing settings.\n\n"
                "```markdown\n## [0.0.1]\nExample heading, not a release.\n```",
            ):
                with self.subTest(instructions=instructions):
                    changelog.write_text(
                        "# Changelog\n\n## [Unreleased]\n\n- TODO: future work\n\n"
                        f"## [0.3.2] - 2026-09-08\n\n{body}{instructions}\n\n"
                        "## [0.3.1] - 2026-09-01\n\n- Historical notes.\n"
                    )
                    expected = f"## v0.3.2\n\n{body}{instructions}\n"
                    self.assertEqual(platform_release.render_release_notes(root), expected)
                    generated = root / "generated.md"
                    generated.write_text("Explicit PR history\n")
                    self.assertEqual(
                        platform_release.render_release_notes(root, generated),
                        expected + "\n## Pull Requests And Contributors\n\nExplicit PR history\n",
                    )
            for invalid_body in ("", "- TODO: selected work"):
                changelog.write_text(f"## [0.3.2] - 2026-09-08\n\n{invalid_body}\n")
                with self.assertRaises(platform_release.ReleaseError):
                    platform_release.render_release_notes(root)
            (root / "VERSION").write_text("0.3.2\n## injected")
            with self.assertRaises(platform_release.ReleaseError):
                platform_release.render_release_notes(root)

    def test_successor_preparation_reuses_notes_and_keeps_provenance(self) -> None:
        authored = "- An authored fix.\n\n### Instructions\n\nKeep this procedure."
        historical = "## [0.3.1] - 2026-09-01\n\n- Historical notes.\n"
        for body, summary in (
            (authored, "Summary"), ("", "Explicit summary"),
            (authored + "\n\n```markdown\n## [0.3.2]\nExample only.\n```", "Summary"),
            (authored + "\n\n~~~markdown\n## [0.3.2]\nExample only.\n~~~", "Summary"),
            ("", ""), ("", "Summary\n## [9.9.9]"), ("- TODO: finish", "Summary"),
        ):
            with self.subTest(body=body, summary=summary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "release").mkdir()
                version = root / "VERSION"
                version.write_text("0.3.1\n")
                config = root / "release/config.yaml"
                config.write_text("{}\n")
                changelog = root / "CHANGELOG.md"
                original = f"# Changelog\n\n## [Unreleased]\n\n{body}\n\n{historical}"
                changelog.write_text(original)
                provenance = {"previousTag": "v0.3.1", "includedThrough": "a" * 40,
                              "commits": ["a" * 40], "compareUrl": "unchanged"}
                args = SimpleNamespace(
                    version="0.3.2", previous_tag="v0.3.1", release_date="2026-09-08",
                    summary=summary, stable_upgrade="supported", recovery="forward-fix",
                    upgrades_from_alpha_revisions="",
                )
                with (
                    mock.patch.multiple(platform_release, ROOT=root, VERSION_PATH=version,
                                        CONFIG_PATH=config, CHANGELOG_PATH=changelog,
                                        MANIFEST_PATH=root / "release/manifest.yaml"),
                    mock.patch.object(platform_release, "latest_release_tag", return_value="v0.3.1"),
                    mock.patch.object(platform_release, "verify_release_tag_signature") as verify,
                    mock.patch.object(platform_release, "provenance_from_git", return_value=provenance),
                    mock.patch.object(platform_release, "build_manifest", return_value={}),
                    mock.patch.object(platform_release, "validate_manifest_schema"),
                ):
                    if not summary or "\n" in summary or "TODO" in body:
                        with self.assertRaises(platform_release.ReleaseError):
                            platform_release.prepare_release(args)
                        self.assertEqual(version.read_text(), "0.3.1\n")
                        self.assertEqual(changelog.read_text(), original)
                        continue
                    platform_release.prepare_release(args)
                    verify.assert_called_once_with("v0.3.1", "v0.3.1")
                    expected_body = body or f"- {summary}"
                    self.assertEqual(changelog.read_text(),
                                     "# Changelog\n\n## [Unreleased]\n\n"
                                     f"## [0.3.2] - 2026-09-08\n\n{expected_body}\n\n{historical}")
                    prepared = yaml.safe_load(config.read_text())
                    self.assertEqual(prepared["provenance"], provenance)
                    migration = (root / "release/migrations/v0.3.2.md").read_text()
                    self.assertEqual(migration, platform_release.migration_scaffold(
                        "0.3.2", "supported", [], "forward-fix"))
                    self.assertFalse(platform_release.contains_todo(migration))
                    platform_release.validate_migration_compatibility(migration, prepared["compatibility"])

                    # Retry from the same predecessor with existing authored evidence.
                    existing = changelog.read_text()
                    for suffix, error in (
                        ("", None),
                        ("\n## [0.3.2] - 2026-09-08\n\n- Duplicate.\n",
                         "duplicate changelog sections for 0.3.2"),
                        ("\n## [0.3.2] - 2026-09-08\n",
                         "duplicate changelog sections for 0.3.2"),
                    ):
                        with self.subTest(existing_suffix=suffix):
                            version.write_text("0.3.1\n")
                            candidate = existing + suffix
                            changelog.write_text(candidate)
                            if error:
                                with self.assertRaisesRegex(platform_release.ReleaseError, error):
                                    platform_release.prepare_release(args)
                                self.assertEqual(version.read_text(), "0.3.1\n")
                            else:
                                platform_release.prepare_release(args)
                            self.assertEqual(changelog.read_text(), candidate)
                    for malformed in ("", "- TODO: complete existing notes"):
                        version.write_text("0.3.1\n")
                        candidate = (
                            "# Changelog\n\n## [Unreleased]\n\n- Keep unreleased.\n\n"
                            f"## [0.3.2] - 2026-09-08\n\n{malformed}\n\n{historical}"
                        )
                        changelog.write_text(candidate)
                        with self.assertRaisesRegex(
                            platform_release.ReleaseError,
                            "existing release changelog section is empty or contains TODO markers",
                        ):
                            platform_release.prepare_release(args)
                        self.assertEqual(version.read_text(), "0.3.1\n")
                        self.assertEqual(changelog.read_text(), candidate)

    def test_publication_uses_one_trusted_compact_renderer(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/publish-release.yaml").read_text())
        steps = workflow["jobs"]["publish"]["steps"]
        renderers = [step for step in steps if "platform_release.py notes" in step.get("run", "")]
        self.assertEqual(len(renderers), 1)
        self.assertEqual(renderers[0]["working-directory"], "release-tooling")
        self.assertIn("--release-root ../release-data", renderers[0]["run"])
        commands = "\n".join(step.get("run", "") for step in steps)
        self.assertNotIn("generate-notes", commands)
        self.assertNotIn("--generated-notes", commands)
        self.assertIn('--notes-file "$RUNNER_TEMP/release-notes.md"', commands)

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
        self.assertEqual(scaffold,
                         "# Platform v0.1.2\n\n## Support\n\n"
                         "- Stable upgrades: Supported.\n"
                         "- Supported alpha source revisions: None.\n"
                         "- Downgrade: Unsupported.\n\n## Breaking Changes\n\n"
                         "See the release notes in CHANGELOG.md for breaking changes and required actions.\n\n"
                         "## Recovery\n\nRecovery classification: Forward fix.\n")
        self.assertFalse(platform_release.contains_todo(scaffold))
        supported = scaffold
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
                    "See the release notes in CHANGELOG.md for breaking changes and required actions.",
                    "",
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
