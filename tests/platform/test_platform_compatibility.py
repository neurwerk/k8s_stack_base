"""Shared adoption gates, using only synthetic inputs and local Git repositories."""

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import yaml

from scripts import check_platform_compatibility as gate

ROOT = Path(__file__).resolve().parents[2]
CLIENT = Path("/example/client")
BASE, HEAD, MERGE = (letter * 40 for letter in "abc")
OLD, MAIN, TARGET = (digit * 40 for digit in "123")
FINGERPRINT = "SHA256:" + "A" * 43


def source(revision="v1.2.3", *, mode="upgrade", promoted=None):
    if revision == "main":
        selector = "branch"
    elif len(revision) == 40:
        selector = "commit"
    else:
        selector = "tag"
    alpha = selector != "tag"
    annotations = (
        {gate.CHANNEL_ANNOTATION: "alpha"}
        if alpha
        else {
            gate.ADOPTION_MODE_ANNOTATION: mode,
            gate.ADOPTION_TARGET_ANNOTATION: revision,
        }
    )
    if promoted is not None:
        annotations[gate.PROMOTED_FROM_ALPHA_ANNOTATION] = promoted
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "GitRepository",
        "metadata": {"name": "k8s-stack", "namespace": "flux-system", "annotations": annotations},
        "spec": {
            "interval": "30s",
            "url": gate.PLATFORM_SOURCE_URL,
            "ref": {selector: revision},
            "verify": {
                "mode": "HEAD" if alpha else "Tag",
                "secretRef": {
                    "name": "k8s-stack-alpha-trust" if alpha else "k8s-stack-release-trust"
                },
            },
        },
    }


def controls():
    result = {gate.FLUX_COMPONENTS_PATH: "generated bootstrap components\n"}
    for path, resources in (
        (gate.CLUSTER_KUSTOMIZATION_PATH, gate.CLUSTER_RESOURCES),
        (gate.FLUX_KUSTOMIZATION_PATH, gate.FLUX_RESOURCES),
    ):
        result[path] = yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": resources,
            }
        )
    result[gate.FLUX_SYNC_PATH] = yaml.safe_dump_all(
        [
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "GitRepository",
                "metadata": {"name": "flux-system", "namespace": "flux-system"},
                "spec": {
                    "interval": "1m0s",
                    "ref": {"branch": "main"},
                    "secretRef": {"name": "flux-system"},
                    "url": "ssh://git@github.com/neurwerk/k8s_stack_client_example.git",
                },
            },
            {
                "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
                "kind": "Kustomization",
                "metadata": {"name": "flux-system", "namespace": "flux-system"},
                "spec": {
                    "interval": "10m0s",
                    "path": "./clusters/prod-eu-1",
                    "prune": True,
                    "sourceRef": {"kind": "GitRepository", "name": "flux-system"},
                },
            },
        ]
    )
    return result


def manifest(tag="v1.2.4", **compatibility):
    return {
        "apiVersion": "platform.neurwerk.com/v1alpha1",
        "kind": "PlatformRelease",
        "metadata": {"name": tag},
        "spec": {
            "version": tag[1:],
            "trust": {"algorithm": "ssh-ed25519", "fingerprint": FINGERPRINT},
            "compatibility": {
                "stableUpgrade": "supported",
                "downgrade": "unsupported",
                "recovery": "forward-fix",
                **compatibility,
            },
        },
    }


def release(tag="v1.2.4"):
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-01-01T00:00:00Z",
    }


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.files = {revision: controls() for revision in (BASE, HEAD)}
        self.files[BASE][gate.PLATFORM_SOURCE_PATH] = yaml.safe_dump(source())
        self.files[HEAD][gate.PLATFORM_SOURCE_PATH] = yaml.safe_dump(source("v1.2.4"))
        self.tag = Mock(return_value=gate.VerifiedTag(yaml.safe_dump(manifest()), "", TARGET))
        self.publication = Mock(return_value=release())
        self.resolve = Mock(
            side_effect=lambda selector, revision: {
                "branch": MAIN,
                "tag": TARGET,
                "commit": revision,
            }[selector]
        )
        self.ancestor = Mock(
            side_effect=lambda a, b: (a, b) in {(OLD, MAIN), (OLD, TARGET), (MAIN, TARGET)}
        )
        self.ignores = Mock(return_value=[])

    def check(self, old=None, new=None, **kwargs):
        if old is not None:
            self.files[BASE][gate.PLATFORM_SOURCE_PATH] = yaml.safe_dump(old)
        if new is not None:
            self.files[HEAD][gate.PLATFORM_SOURCE_PATH] = yaml.safe_dump(new)

        def read(root, revision, path):
            self.assertEqual(root, CLIENT)
            return self.files[revision][path]

        return gate.run_check(
            CLIENT,
            BASE,
            HEAD,
            kwargs.pop("fingerprint", FINGERPRINT),
            None,
            revision_reader=read,
            source_ignore_finder=self.ignores,
            tag_fetcher=self.tag,
            release_fetcher=self.publication,
            base_revision_fetcher=self.resolve,
            ancestor_checker=self.ancestor,
            **kwargs,
        )

    def validate(
        self, document=None, notes="", publication=None, old="v1.2.3", new="v1.2.4", mode="upgrade"
    ):
        gate.validate_release_contract(
            old,
            new,
            yaml.safe_dump(document if document is not None else manifest(new)),
            notes,
            publication if publication is not None else release(new),
            mode,
            FINGERPRINT,
        )

    def test_stable_classification_verification_and_unchanged(self):
        result = self.check(classify_only=True)
        self.assertTrue(result.changed)
        self.assertFalse(result.verified)
        self.tag.assert_not_called()
        self.publication.assert_not_called()
        self.assertTrue(self.check().verified)
        self.tag.assert_called_once_with("v1.2.4", FINGERPRINT)
        self.publication.assert_called_once_with("v1.2.4", None)
        self.tag.reset_mock()
        self.publication.reset_mock()
        self.assertFalse(self.check(new=source()).changed)
        self.tag.assert_not_called()
        self.publication.assert_not_called()
        self.resolve.assert_not_called()
        with self.assertRaisesRegex(gate.CompatibilityError, "SIGNER_FINGERPRINT"):
            self.check(fingerprint="")

    def test_stable_transition_rejections_happen_before_classification(self):
        cases = {
            "downgrade": source("v1.2.2"),
            "annotation-only change": source(mode="fresh-install"),
            "stable promotion annotation": source("v1.2.4", promoted=OLD),
            "retired bypass": source("v1.2.4", mode="manual-override"),
        }
        for name, proposed in cases.items():
            with self.subTest(name=name), self.assertRaises(gate.CompatibilityError):
                self.check(new=proposed, classify_only=True)
        self.tag.assert_not_called()

    def test_closed_source_and_exact_adoption_intent(self):
        mutations = {
            "identity": ("metadata.name", "other"),
            "namespace": ("metadata.namespace", "other"),
            "API": ("apiVersion", "source.toolkit.fluxcd.io/v1beta1"),
            "kind": ("kind", "Bucket"),
            "URL": ("spec.url", "https://example.com/other.git"),
            "interval": ("spec.interval", "1m"),
            "trust": ("spec.verify.secretRef.name", "other"),
            "verification": ("spec.verify.mode", "HEAD"),
            "include": ("spec.include", []),
            "ignore": ("spec.ignore", "*"),
            "suspend": ("spec.suspend", True),
            "branch and tag": ("spec.ref.branch", "main"),
            "unknown mode": (
                "metadata.annotations",
                {
                    gate.ADOPTION_MODE_ANNOTATION: "review-required",
                    gate.ADOPTION_TARGET_ANNOTATION: "v1.2.3",
                },
            ),
            "wrong target": (
                "metadata.annotations",
                {
                    gate.ADOPTION_MODE_ANNOTATION: "upgrade",
                    gate.ADOPTION_TARGET_ANNOTATION: "v1.2.2",
                },
            ),
            "bad promotion type": (
                "metadata.annotations",
                {
                    gate.ADOPTION_MODE_ANNOTATION: "upgrade",
                    gate.ADOPTION_TARGET_ANNOTATION: "v1.2.3",
                    gate.PROMOTED_FROM_ALPHA_ANNOTATION: [],
                },
            ),
        }
        for name, (path, value) in mutations.items():
            document = source()
            node = document
            for part in path.split(".")[:-1]:
                node = node[part]
            node[path.split(".")[-1]] = value
            with self.subTest(name=name), self.assertRaises(gate.CompatibilityError):
                gate.parse_platform_source(yaml.safe_dump(document), origin="test")
        for invalid in (
            None,
            123,
            "1.2.3",
            "v01.2.3",
            "v1.2",
            "v1.2.3-rc.1",
            ">=v1.2.3",
            "v1.2.\u0663",
        ):
            with self.subTest(tag=invalid), self.assertRaises(gate.CompatibilityError):
                gate.parse_tag(invalid, field="tag")

    def test_stable_only_gates_both_sources_even_unchanged(self):
        for old, new in (
            (source("main"), source()),
            (source(), source(MAIN)),
            (source("main"), source("main")),
        ):
            with self.subTest(old=old["spec"]["ref"], new=new["spec"]["ref"]):
                with self.assertRaisesRegex(gate.CompatibilityError, "allow-alpha"):
                    self.check(old, new, classify_only=True)
        self.resolve.assert_not_called()
        self.tag.assert_not_called()

    def test_alpha_source_shape_and_separate_trust(self):
        for revision in ("main", MAIN):
            parsed = gate.parse_platform_source(
                yaml.safe_dump(source(revision)), origin="test", allow_alpha=True
            )
            self.assertEqual(parsed.channel, "alpha")
            self.assertEqual(parsed.revision, revision)
        for name, change in {
            "branch": {"ref": {"branch": "candidate"}},
            "short SHA": {"ref": {"commit": "abc123"}},
            "two refs": {"ref": {"branch": "main", "commit": MAIN}},
            "stable trust": {
                "verify": {"mode": "HEAD", "secretRef": {"name": "k8s-stack-release-trust"}}
            },
            "tag verification": {
                "verify": {"mode": "Tag", "secretRef": {"name": "k8s-stack-alpha-trust"}}
            },
        }.items():
            document = source("main")
            document["spec"].update(change)
            with self.subTest(name=name), self.assertRaises(gate.CompatibilityError):
                gate.parse_platform_source(
                    yaml.safe_dump(document), origin="test", allow_alpha=True
                )

    def test_control_plane_cannot_hide_or_transform_the_checked_source(self):
        for revision in (BASE, HEAD):
            for path in (gate.CLUSTER_KUSTOMIZATION_PATH, gate.FLUX_KUSTOMIZATION_PATH):
                for field, value in {
                    "patches": [],
                    "resources": [],
                    "components": ["other"],
                }.items():
                    original = self.files[revision][path]
                    document = yaml.safe_load(original)
                    document[field] = value
                    self.files[revision][path] = yaml.safe_dump(document)
                    with (
                        self.subTest(revision=revision, path=path, field=field),
                        self.assertRaisesRegex(gate.CompatibilityError, "transform-free"),
                    ):
                        self.check(classify_only=True)
                    self.files[revision][path] = original
            self.ignores.side_effect = lambda root, sha: (
                ["sensitive-path/.sourceignore"] if sha == revision else []
            )
            with self.assertRaisesRegex(
                gate.CompatibilityError, "prohibited .sourceignore"
            ) as raised:
                self.check(classify_only=True)
            self.assertNotIn("sensitive-path", str(raised.exception))
            self.ignores.side_effect = None
        for name, index, field, value in (
            ("patch", 1, "patches", []),
            ("path", 1, "path", "./other"),
            ("source", 1, "sourceRef", {"kind": "GitRepository", "name": "other"}),
            ("branch", 0, "ref", {"branch": "candidate"}),
            ("ignore", 0, "ignore", "*"),
            ("URL", 0, "url", "https://example.com/other.git"),
        ):
            sync = list(yaml.safe_load_all(controls()[gate.FLUX_SYNC_PATH]))
            sync[index]["spec"][field] = value
            self.files[HEAD][gate.FLUX_SYNC_PATH] = yaml.safe_dump_all(sync)
            with self.subTest(name=name), self.assertRaises(gate.CompatibilityError):
                self.check(classify_only=True)
        self.files[HEAD] = {
            **controls(),
            gate.PLATFORM_SOURCE_PATH: yaml.safe_dump(source("v1.2.4")),
        }
        for path, content in {
            gate.FLUX_COMPONENTS_PATH: "different components",
            gate.FLUX_SYNC_PATH: controls()[gate.FLUX_SYNC_PATH].replace(
                "client_example", "client_sample"
            ),
        }.items():
            original = self.files[HEAD][path]
            self.files[HEAD][path] = content
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(gate.CompatibilityError, "bootstrap controls"),
            ):
                self.check(classify_only=True)
            self.files[HEAD][path] = original

    def test_forward_upgrade_and_legacy_contracts(self):
        cases = [
            ("skipped versions", "v0.9.0", "v1.2.4", "supported", "upgrade", True),
            ("fresh only", "v1.2.3", "v1.2.4", "fresh-install-only", "upgrade", False),
            ("fresh installation", "v1.2.3", "v1.2.4", "fresh-install-only", "fresh-install", True),
            ("downgrade even fresh", "v1.2.4", "v1.2.3", "supported", "fresh-install", False),
            ("unchanged", "v1.2.4", "v1.2.4", "supported", "upgrade", False),
        ]
        for name, old, new, policy, mode, succeeds in cases:
            with self.subTest(name=name):
                if succeeds:
                    self.validate(manifest(new, stableUpgrade=policy), old=old, new=new, mode=mode)
                else:
                    with self.assertRaises(gate.CompatibilityError):
                        self.validate(
                            manifest(new, stableUpgrade=policy), old=old, new=new, mode=mode
                        )
        for target, sources, succeeds in (
            ("v0.1.1", ["v0.1.0"], True),
            ("v0.1.1", [], False),
            ("v1.2.4", ["v0.1.0"], False),
        ):
            document = manifest(target)
            compatibility = document["spec"]["compatibility"]
            del compatibility["stableUpgrade"]
            compatibility["upgradesFrom"] = sources
            with self.subTest(target=target, sources=sources):
                if succeeds:
                    self.validate(document, old="v0.1.0", new=target)
                else:
                    with self.assertRaises(gate.CompatibilityError):
                        self.validate(document, old="v0.1.0", new=target)
        document = manifest("v0.1.0")
        document["spec"]["compatibility"] = {
            "upgradesFrom": [],
            "freshInstall": "supported",
            "downgrade": "unsupported",
            "recovery": "replacement-restore",
        }
        gate.validate_release_contract(
            "v0.1.0",
            "v0.1.0",
            yaml.safe_dump(document),
            "",
            release("v0.1.0"),
            "fresh-install",
            FINGERPRINT,
            validate_transition=False,
        )
        for field, value in (
            ("stableUpgrade", "supported"),
            ("upgradesFrom", "v0.1.0"),
            ("upgradesFrom", [123]),
            ("upgradesFrom", ["v0.01.0"]),
        ):
            compatibility = {**document["spec"]["compatibility"], field: value}
            invalid = manifest("v0.1.1")
            invalid["spec"]["compatibility"] = compatibility
            with (
                self.subTest(legacy_field=field, value=value),
                self.assertRaises(gate.CompatibilityError),
            ):
                self.validate(invalid, old="v0.1.0", new="v0.1.1")

    def test_manifest_identity_trust_and_policy_fail_closed(self):
        cases = {
            "API": ("apiVersion", "other"),
            "kind": ("kind", "other"),
            "name": ("metadata.name", "v9.9.9"),
            "version": ("spec.version", "9.9.9"),
            "signer": ("spec.trust.fingerprint", "SHA256:" + "B" * 43),
            "algorithm": ("spec.trust.algorithm", "rsa"),
            "mapping": ("spec.trust", []),
            "missing policy": ("spec.compatibility.stableUpgrade", None),
            "unknown recovery": ("spec.compatibility.recovery", "snapshot"),
            "unknown downgrade": ("spec.compatibility.downgrade", "conditional"),
            "supported downgrade": ("spec.compatibility.downgrade", "supported"),
            "mixed policy": ("spec.compatibility.upgradesFrom", []),
        }
        for name, (path, value) in cases.items():
            document = manifest()
            node = document
            for part in path.split(".")[:-1]:
                node = node[part]
            node[path.split(".")[-1]] = value
            with self.subTest(name=name), self.assertRaises(gate.CompatibilityError):
                self.validate(document)
        for field, value in (
            ("tag_name", "v9.9.9"),
            ("draft", True),
            ("prerelease", True),
            ("published_at", None),
            ("draft", None),
            ("draft", 0),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(gate.CompatibilityError):
                self.validate(publication={**release(), field: value})

    def test_optional_notes_but_explicit_declarations_must_agree(self):
        for notes in ("", "Plain notes.", "## Support\n", "- Downgrade: Unsupported.\n"):
            self.validate(notes=notes)
        for display, value in gate.RECOVERY_DISPLAY_VALUES.items():
            self.validate(manifest(recovery=value), notes=f"Recovery classification: {display}.\n")
        cases = {
            "title": "# Platform v9.9.9\n",
            "duplicate heading": "## Support\n\n## Support\n",
            "stable mismatch": "- Stable upgrades: Fresh installation only.",
            "legacy mismatch": "- Supported source versions: None.",
            "downgrade mismatch": "- Downgrade: Supported.",
            "recovery mismatch": "Recovery classification: Replacement restore.",
        }
        for prefix, value in (
            ("- Stable upgrades:", "Supported"),
            ("- Downgrade:", "Unsupported"),
            ("Recovery classification:", "Forward fix"),
        ):
            cases[f"duplicate {prefix}"] = f"{prefix} {value}.\n{prefix} {value}."
            cases[f"unknown {prefix}"] = f"{prefix} Conditional."
            cases[f"malformed {prefix}"] = f"{prefix} {value}"
        for name, notes in cases.items():
            with self.subTest(name=name), self.assertRaises(gate.CompatibilityError):
                self.validate(notes=notes)
        self.assertEqual(
            gate.parse_support_contract("- Supported source versions: `v0.1.0`."),
            ({"v0.1.0"}, None),
        )
        for notes in (
            "- Supported source versions: `v0.1.0`, `v0.1.0`.",
            "- Supported source versions: v0.1.0.",
            "- Stable upgrades: Supported.\n- Supported source versions: None.",
        ):
            with self.assertRaises(gate.CompatibilityError):
                gate.parse_support_contract(notes)

    def test_alpha_freeze_unfreeze_forward_and_unchanged(self):
        for name, old, new, changed in (
            ("freeze", "main", MAIN, True),
            ("unfreeze", OLD, "main", True),
            ("forward pin", OLD, MAIN, True),
            ("unchanged branch", "main", "main", False),
        ):
            with self.subTest(name=name):
                self.resolve.reset_mock()
                result = self.check(source(old), source(new), allow_alpha=True, fingerprint="")
                self.assertEqual(result.changed, changed)
                self.assertEqual(result.verified, changed)
                self.assertEqual(result.new_alpha_commit, MAIN)
                self.assertEqual(
                    self.resolve.call_args_list.count(unittest.mock.call("branch", "main")), 1
                )
        self.tag.assert_not_called()
        for name, old, new in (
            ("stale freeze", "main", OLD),
            ("backward pin", MAIN, OLD),
            ("outside main", OLD, TARGET),
            ("moving promotion", "main", "v1.2.4"),
        ):
            with self.subTest(name=name), self.assertRaises(gate.CompatibilityError):
                self.check(source(old), source(new), allow_alpha=True, classify_only=True)
        for name in ("old", "new"):
            with (
                self.subTest(expected=name),
                self.assertRaisesRegex(gate.CompatibilityError, f"resolved {name} alpha"),
            ):
                self.check(
                    source("main"),
                    source(MAIN),
                    allow_alpha=True,
                    **{f"expected_{name}_alpha_commit": OLD},
                )

    def test_stable_to_alpha_authenticates_baseline_even_when_classifying(self):
        self.tag.return_value = gate.VerifiedTag("", "", OLD)
        for classify in (True, False):
            result = self.check(source(), source("main"), allow_alpha=True, classify_only=classify)
            self.assertEqual(result.verified, not classify)
            self.tag.assert_called_with("v1.2.3", FINGERPRINT)
        self.publication.assert_not_called()
        self.ancestor.return_value = False
        self.ancestor.side_effect = None
        with self.assertRaisesRegex(gate.CompatibilityError, "authenticated stable"):
            self.check(allow_alpha=True, classify_only=True)
        self.tag.side_effect = gate.CompatibilityError("signature rejected")
        with self.assertRaisesRegex(gate.CompatibilityError, "signature rejected"):
            self.check(allow_alpha=True, classify_only=True)

    def test_alpha_promotion_contract_cases(self):
        cases = [
            ("exact", OLD, None, "", "upgrade", True),
            ("forward missing allowlist", TARGET, None, "", "upgrade", False),
            ("forward absent revision", TARGET, [], "", "upgrade", False),
            (
                "forward supported",
                TARGET,
                [OLD],
                f"- Supported alpha source revisions: `{OLD}`.",
                "upgrade",
                True,
            ),
            (
                "notes mismatch",
                TARGET,
                [OLD, MAIN],
                f"- Supported alpha source revisions: `{OLD}`.",
                "upgrade",
                False,
            ),
            ("fresh forward", TARGET, [], "", "fresh-install", True),
            (
                "exact still checks recovery",
                OLD,
                None,
                "Recovery classification: Replacement restore.",
                "upgrade",
                False,
            ),
            ("duplicate alpha manifest", TARGET, [OLD, OLD], "", "upgrade", False),
            ("malformed alpha manifest", TARGET, [123], "", "fresh-install", False),
        ]
        for name, commit, revisions, notes, mode, succeeds in cases:
            document = manifest()
            if revisions is not None:
                document["spec"]["compatibility"]["upgradesFromAlphaRevisions"] = revisions
            self.tag.return_value = gate.VerifiedTag(yaml.safe_dump(document), notes, commit)
            with self.subTest(name=name):
                if succeeds:
                    self.assertTrue(
                        self.check(
                            source(OLD), source("v1.2.4", mode=mode, promoted=OLD), allow_alpha=True
                        ).verified
                    )
                else:
                    with self.assertRaises(gate.CompatibilityError):
                        self.check(
                            source(OLD), source("v1.2.4", mode=mode, promoted=OLD), allow_alpha=True
                        )
        for declaration in (
            f"`{OLD}`, `{OLD}`.",
            "abc123.",
            "None",
            "None.\n- Supported alpha source revisions: None.",
        ):
            with self.subTest(declaration=declaration), self.assertRaises(gate.CompatibilityError):
                gate.parse_alpha_source_revisions(
                    "- Supported alpha source revisions: " + declaration
                )
        self.assertEqual(
            gate.parse_alpha_source_revisions("- Supported alpha source revisions: None."), set()
        )

    def test_alpha_promotion_observation_ancestry_and_classification(self):
        result = self.check(
            source(OLD), source("v1.2.4", promoted=OLD), allow_alpha=True, classify_only=True
        )
        self.assertFalse(result.verified)
        self.tag.assert_not_called()
        self.publication.assert_not_called()
        with self.assertRaisesRegex(gate.CompatibilityError, "exact observed"):
            self.check(new=source("v1.2.4", promoted=MAIN), allow_alpha=True, classify_only=True)
        self.ancestor.side_effect = lambda a, b: (a, b) == (OLD, MAIN)
        with self.assertRaisesRegex(gate.CompatibilityError, "stable target is behind"):
            self.check(new=source("v1.2.4", promoted=OLD), allow_alpha=True, classify_only=True)

    def test_tag_authentication_order_and_failure_branches(self):
        for failure in (
            None,
            "lightweight",
            "key type",
            "fingerprint",
            "signature",
            "peeled commit",
        ):
            calls = []

            def command(args, **kwargs):
                calls.append(args)
                if "cat-file" in args:
                    return "commit" if failure == "lightweight" else "tag"
                if args[0] == "ssh-keygen":
                    return "256 " + (
                        "SHA256:" + "B" * 43 if failure == "fingerprint" else FINGERPRINT
                    )
                if "verify-tag" in args and failure == "signature":
                    raise gate.CompatibilityError("signature rejected")
                if "verify-tag" in args:
                    self.assertTrue(any(call[0] == "ssh-keygen" for call in calls))
                    self.assertIn("gpg.ssh.allowedSignersFile=", args[2])
                if "show" in args:
                    if args[-1].endswith(gate.TRUST_KEY_PATH):
                        return (
                            "ssh-rsa synthetic"
                            if failure == "key type"
                            else "ssh-ed25519 synthetic"
                        )
                    self.assertTrue(any("verify-tag" in call for call in calls))
                    return "release content"
                if "ls-tree" in args:
                    return ""
                if "rev-parse" in args:
                    return "short" if failure == "peeled commit" else TARGET
                return ""

            with (
                self.subTest(failure=failure),
                patch.object(gate, "run_command", side_effect=command),
            ):
                if failure is None:
                    self.assertEqual(
                        gate.fetch_and_verify_tag("v1.2.4", FINGERPRINT),
                        gate.VerifiedTag("release content", "", TARGET),
                    )
                else:
                    with self.assertRaises(gate.CompatibilityError):
                        gate.fetch_and_verify_tag("v1.2.4", FINGERPRINT)
                if failure in ("lightweight", "key type", "fingerprint", "signature"):
                    self.assertFalse(
                        any("release/manifest.yaml" in arg for call in calls for arg in call)
                    )

    def test_safe_resolution_size_limits_and_ancestry_exit_codes(self):
        with patch.object(gate, "run_command") as command:
            for selector, revision in (
                ("branch", "candidate"),
                ("commit", "abc123"),
                ("tag", "--help"),
            ):
                with self.subTest(selector=selector), self.assertRaises(gate.CompatibilityError):
                    gate.fetch_base_revision(selector, revision)
            self.assertEqual(gate.fetch_base_revision("commit", OLD), OLD)
            command.assert_not_called()
            command.return_value = MAIN + "\trefs/heads/main\n"
            self.assertEqual(gate.fetch_base_revision("branch", "main"), MAIN)
            command.assert_called_with(
                ["git", "ls-remote", gate.PLATFORM_SOURCE_URL, "refs/heads/main"]
            )
            command.return_value = TARGET + "\trefs/tags/v1.2.4^{}\n"
            self.assertEqual(gate.fetch_base_revision("tag", "v1.2.4"), TARGET)
            command.assert_called_with(
                ["git", "ls-remote", gate.PLATFORM_SOURCE_URL, "refs/tags/v1.2.4^{}"]
            )
            for size in ("not an integer", str(gate.MAX_REVISION_FILE_BYTES + 1)):
                command.reset_mock()
                command.return_value = size
                with self.assertRaises(gate.CompatibilityError):
                    gate.read_at_revision(CLIENT, BASE, gate.PLATFORM_SOURCE_PATH)
                self.assertEqual(command.call_count, 1, "oversized content must not be read")
        for code in (0, 1, 2):
            with (
                self.subTest(code=code),
                patch.object(gate, "run_command"),
                patch.object(
                    gate.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], code, "", "private-output"),
                ),
            ):
                if code == 2:
                    with self.assertRaises(gate.CompatibilityError) as raised:
                        gate.is_base_ancestor(OLD, MAIN)
                    self.assertNotIn("private-output", str(raised.exception))
                else:
                    self.assertEqual(gate.is_base_ancestor(OLD, MAIN), code == 0)

    def test_release_api_is_fixed_and_rejects_non_mapping_responses(self):
        for payload in (release(), []):
            with patch.object(
                gate, "urlopen", return_value=io.StringIO(json.dumps(payload))
            ) as opener:
                if isinstance(payload, dict):
                    self.assertEqual(gate.fetch_release("v1.2.4", "synthetic-token"), payload)
                else:
                    with self.assertRaises(gate.CompatibilityError):
                        gate.fetch_release("v1.2.4", "synthetic-token")
                request = opener.call_args.args[0]
                self.assertEqual(request.full_url, gate.PLATFORM_RELEASE_API + "v1.2.4")
                self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-token")
                self.assertEqual(opener.call_args.kwargs["timeout"], 20)

    def test_network_and_command_errors_never_disclose_payloads(self):
        marker = "sensitive-payload.example.com"
        for error in (
            URLError(marker),
            TimeoutError(marker),
            json.JSONDecodeError(marker, marker, 0),
            HTTPError("https://example.com", 403, marker, {}, None),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(gate, "urlopen", side_effect=error),
            ):
                with self.assertRaises(gate.CompatibilityError) as raised:
                    gate.fetch_release("v1.2.4", "synthetic-token")
                self.assertNotIn(marker, str(raised.exception))
        for outcome in (
            subprocess.CompletedProcess([], 1, marker, marker),
            subprocess.TimeoutExpired([marker], 60),
            OSError(marker),
        ):
            with patch.object(gate.subprocess, "run") as run:
                if isinstance(outcome, Exception):
                    run.side_effect = outcome
                else:
                    run.return_value = outcome
                with self.assertRaises(gate.CompatibilityError) as raised:
                    gate.run_command(["git", marker])
                self.assertNotIn(marker, str(raised.exception))
                self.assertEqual(run.call_args.kwargs["timeout"], gate.COMMAND_TIMEOUT_SECONDS)

    def test_exact_merge_tuple_rejects_stale_head_merge_and_parents(self):
        cases = {
            "head": (OLD, MERGE, f"{MERGE} {BASE} {HEAD}"),
            "merge": (HEAD, OLD, f"{MERGE} {BASE} {HEAD}"),
            "base": (HEAD, MERGE, f"{MERGE} {OLD} {HEAD}"),
            "reordered parents": (HEAD, MERGE, f"{MERGE} {HEAD} {BASE}"),
            "extra parent": (HEAD, MERGE, f"{MERGE} {BASE} {HEAD} {OLD}"),
        }
        for name, values in cases.items():
            with (
                self.subTest(name=name),
                patch.object(gate, "run_command", side_effect=["", *values]),
            ):
                with self.assertRaises(gate.CompatibilityError):
                    gate.fetch_pull_request_refs(CLIENT, 12, BASE, HEAD, MERGE)
        with patch.object(gate, "run_command") as run:
            for number, sha in ((0, HEAD), (12, "--help"), (12, "a" * 41)):
                with self.assertRaises(gate.CompatibilityError):
                    gate.fetch_pull_request_refs(CLIENT, number, BASE, sha, MERGE)
            run.assert_not_called()

    def test_real_git_shallow_merge_parents_and_revision_data(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            origin, checker = Path(temporary) / "origin", Path(temporary) / "checker"
            origin.mkdir()
            checker.mkdir()

            def git(*args, cwd=origin):
                return gate.run_command(
                    [
                        "git",
                        "-c",
                        "user.name=Platform Test",
                        "-c",
                        "user.email=platform-test@example.invalid",
                        "-c",
                        "commit.gpgsign=false",
                        *args,
                    ],
                    cwd=cwd,
                ).strip()

            git("init", "--quiet")
            (origin / "nested space").mkdir()
            (origin / "nested space/.sourceignore").write_text("*.yaml\n")
            git("add", ".")
            git("commit", "--quiet", "-m", "base")
            base = git("rev-parse", "HEAD")
            git("commit", "--quiet", "--allow-empty", "-m", "head")
            head = git("rev-parse", "HEAD")
            git("branch", "proposal", head)
            git("switch", "--quiet", "--detach", base)
            git("merge", "--quiet", "--no-ff", "proposal", "-m", "test merge")
            merge = git("rev-parse", "HEAD")
            git("update-ref", "refs/pull/12/head", head)
            git("update-ref", "refs/pull/12/merge", merge)
            git("init", "--quiet", cwd=checker)
            git("remote", "add", "origin", origin.as_uri(), cwd=checker)
            git("fetch", "--quiet", "--depth=1", "origin", merge, cwd=checker)
            self.assertEqual(
                git("rev-list", "--parents", "-n", "1", "FETCH_HEAD", cwd=checker), merge
            )
            gate.fetch_pull_request_refs(checker, 12, base, head, merge)
            self.assertEqual(
                git("rev-list", "--parents", "-n", "1", merge, cwd=checker),
                f"{merge} {base} {head}",
            )
            self.assertEqual(
                gate.source_ignore_paths(checker, merge), ["nested space/.sourceignore"]
            )
            self.assertEqual(
                gate.read_at_revision(checker, merge, Path("nested space/.sourceignore")),
                "*.yaml\n",
            )

    def test_cli_requires_root_binds_merge_and_emits_only_common_outputs(self):
        args = ["--base-sha", BASE, "--head-sha", HEAD]
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gate.main(args)
        result = gate.CheckResult(
            gate.PlatformSource("alpha", "commit", OLD),
            gate.PlatformSource("stable", "tag", "v1.2.4", "upgrade", OLD),
            OLD,
            None,
            True,
            False,
        )
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            output = Path(temporary) / "output"
            with (
                patch.object(gate, "run_check", return_value=result) as check,
                patch.object(gate, "fetch_pull_request_refs") as fetch,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    gate.main(
                        [
                            *args,
                            "--root",
                            str(CLIENT),
                            "--merge-sha",
                            MERGE,
                            "--pull-request-number",
                            "12",
                            "--allow-alpha",
                            "--classify-only",
                            "--expected-old-alpha-commit",
                            OLD,
                            "--github-output",
                            str(output),
                        ]
                    ),
                    0,
                )
                fetch.assert_called_once_with(CLIENT, 12, BASE, HEAD, MERGE)
                self.assertEqual(check.call_args.args[:3], (CLIENT, BASE, MERGE))
                self.assertTrue(check.call_args.kwargs["allow_alpha"])
            self.assertEqual(
                dict(line.split("=", 1) for line in output.read_text().splitlines()),
                {
                    "old_source_channel": "alpha",
                    "old_source_selector": "commit",
                    "old_source_revision": OLD,
                    "new_source_channel": "stable",
                    "new_source_selector": "tag",
                    "new_source_revision": "v1.2.4",
                    "old_alpha_commit": OLD,
                    "new_alpha_commit": "",
                    "adoption_mode": "upgrade",
                    "platform_source_changed": "true",
                    "platform_verified": "false",
                },
            )
        with patch.object(gate, "run_check") as check, redirect_stderr(io.StringIO()):
            self.assertEqual(
                gate.main([*args, "--root", str(CLIENT), "--pull-request-number", "12"]), 1
            )
            check.assert_not_called()
        with (
            patch.object(gate, "run_check", return_value=result) as check,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(gate.main([*args, "--root", str(CLIENT)]), 0)
            self.assertFalse(check.call_args.kwargs["allow_alpha"])
        for content in (
            "metadata: [sensitive-payload",
            "spec: 2026-99-99\n# sensitive-payload",
            "[]",
        ):
            with (
                self.subTest(content=content),
                self.assertRaises(gate.CompatibilityError) as raised,
            ):
                gate.parse_platform_source(content, origin="test")
            self.assertNotIn("sensitive-payload", str(raised.exception))
        stderr = io.StringIO()
        with (
            patch.object(gate, "run_check", side_effect=OSError("sensitive-path")),
            redirect_stderr(stderr),
        ):
            self.assertEqual(gate.main([*args, "--root", str(CLIENT)]), 1)
        self.assertNotIn("sensitive-path", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
