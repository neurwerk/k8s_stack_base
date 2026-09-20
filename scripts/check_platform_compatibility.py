#!/usr/bin/env python3
"""Verify that a client pull request selects an authentic, compatible platform."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml

PLATFORM_SOURCE_PATH = Path("clusters/prod-eu-1/platform-source.yaml")
CLUSTER_KUSTOMIZATION_PATH = Path("clusters/prod-eu-1/kustomization.yaml")
FLUX_KUSTOMIZATION_PATH = Path("clusters/prod-eu-1/flux-system/kustomization.yaml")
FLUX_SYNC_PATH = Path("clusters/prod-eu-1/flux-system/gotk-sync.yaml")
FLUX_COMPONENTS_PATH = Path("clusters/prod-eu-1/flux-system/gotk-components.yaml")
PLATFORM_SOURCE_URL = "https://github.com/neurwerk/k8s_stack_base.git"
PLATFORM_RELEASE_API = "https://api.github.com/repos/neurwerk/k8s_stack_base/releases/tags/"
TRUST_KEY_PATH = "release/trust/platform-release.sshpub"
ADOPTION_MODE_ANNOTATION = "platform.neurwerk.com/adoption-mode"
ADOPTION_TARGET_ANNOTATION = "platform.neurwerk.com/adoption-target"
CHANNEL_ANNOTATION = "platform.neurwerk.com/channel"
PROMOTED_FROM_ALPHA_ANNOTATION = "platform.neurwerk.com/promoted-from-alpha"
ADOPTION_MODES = {"fresh-install", "upgrade"}
CLUSTER_RESOURCES = [
    "cluster-identity.yaml",
    "platform-source.yaml",
    "namespaces.yaml",
    "client-values.yaml",
    "infrastructure.yaml",
    "applications.yaml",
    "flux-system",
]
FLUX_RESOURCES = ["gotk-components.yaml", "gotk-sync.yaml"]
TAG_PATTERN = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
FINGERPRINT_PATTERN = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
SHA_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
CLIENT_SOURCE_URL_PATTERN = re.compile(
    r"^ssh://git@github\.com/neurwerk/k8s_stack_client_[a-z0-9_]+\.git$"
)
MAX_REVISION_FILE_BYTES = 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 60
LEGACY_UPGRADES_FROM_TARGETS = {"v0.1.0", "v0.1.1"}
COMPATIBILITY_DISPLAY_VALUES = {"Supported": "supported", "Unsupported": "unsupported"}
RECOVERY_DISPLAY_VALUES = {
    "Configuration revert": "configuration-revert",
    "Forward fix": "forward-fix",
    "Component native restore": "component-native-restore",
    "Replacement restore": "replacement-restore",
}
STABLE_UPGRADE_DISPLAY_VALUES = {
    "Supported": "supported",
    "Fresh installation only": "fresh-install-only",
}


class CompatibilityError(RuntimeError):
    """A platform transition failed a required review gate."""


@dataclass(frozen=True)
class PlatformSource:
    channel: str
    selector: str
    revision: str
    adoption_mode: str | None = None
    promoted_from_alpha: str | None = None


@dataclass(frozen=True)
class CheckResult:
    old_source: PlatformSource
    new_source: PlatformSource
    old_alpha_commit: str | None
    new_alpha_commit: str | None
    changed: bool
    verified: bool


@dataclass(frozen=True)
class VerifiedTag:
    manifest: str
    migration: str
    commit: str


def run_command(arguments: list[str], *, cwd: Path | None = None) -> str:
    """Bound commands and withhold arguments/output, which can contain client data."""
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CompatibilityError(
            "local command unavailable or timed out; details withheld"
        ) from error
    if result.returncode != 0:
        raise CompatibilityError("local command failed; details withheld")
    return result.stdout


def parse_tag(tag: Any, *, field: str) -> tuple[int, int, int]:
    match = TAG_PATTERN.fullmatch(tag) if isinstance(tag, str) else None
    if match is None:
        raise CompatibilityError(f"{field} must be an exact strict vX.Y.Z tag")
    return tuple(int(part) for part in match.groups())


def parse_platform_source(
    content: str, *, origin: str, allow_alpha: bool = False
) -> PlatformSource:
    """Require a closed source shape; alpha permission comes from trusted code, not YAML."""
    try:
        source = yaml.safe_load(content)
        metadata, spec = source["metadata"], source["spec"]
        annotations, reference = metadata["annotations"], spec["ref"]
        verification = spec["verify"]
        secret_reference = verification["secretRef"]
        if not all(
            isinstance(value, dict)
            for value in (
                source,
                metadata,
                spec,
                annotations,
                reference,
                verification,
                secret_reference,
            )
        ):
            raise CompatibilityError(f"{origin} must use canonical source mappings")
    except (TypeError, KeyError, ValueError, RecursionError, yaml.YAMLError) as error:
        raise CompatibilityError(
            f"cannot parse platform source from {origin}; details withheld"
        ) from error
    if set(source) != {"apiVersion", "kind", "metadata", "spec"}:
        raise CompatibilityError(f"{origin} must use the canonical source shape")
    if source["apiVersion"] != "source.toolkit.fluxcd.io/v1" or source["kind"] != "GitRepository":
        raise CompatibilityError(f"{origin} must use the canonical source API and kind")
    if metadata != {"annotations": annotations, "name": "k8s-stack", "namespace": "flux-system"}:
        raise CompatibilityError(f"{origin} must use canonical source metadata")
    if set(spec) != {"interval", "url", "ref", "verify"}:
        raise CompatibilityError(f"{origin} must use the canonical source spec")
    if spec["interval"] != "30s" or spec["url"] != PLATFORM_SOURCE_URL:
        raise CompatibilityError(
            f"{origin} must retain the canonical interval and public source URL"
        )
    if set(verification) != {"mode", "secretRef"} or set(secret_reference) != {"name"}:
        raise CompatibilityError(f"{origin} must use canonical source verification")

    if annotations == {CHANNEL_ANNOTATION: "alpha"}:
        if not allow_alpha:
            raise CompatibilityError(
                f"{origin} alpha source requires trusted --allow-alpha permission"
            )
        if reference == {"branch": "main"}:
            selector, revision = "branch", "main"
        elif (
            set(reference) == {"commit"}
            and isinstance(reference["commit"], str)
            and COMMIT_SHA_PATTERN.fullmatch(reference["commit"])
        ):
            selector, revision = "commit", reference["commit"]
        else:
            raise CompatibilityError(
                f"{origin} alpha source must select main or one full 40-hex commit"
            )
        if verification["mode"] != "HEAD" or secret_reference["name"] != "k8s-stack-alpha-trust":
            raise CompatibilityError(
                f"{origin} alpha source requires HEAD verification and k8s-stack-alpha-trust"
            )
        return PlatformSource("alpha", selector, revision)

    required = {ADOPTION_MODE_ANNOTATION, ADOPTION_TARGET_ANNOTATION}
    if set(annotations) not in (required, required | {PROMOTED_FROM_ALPHA_ANNOTATION}):
        raise CompatibilityError(f"{origin} must use canonical source annotations")
    if set(reference) != {"tag"}:
        raise CompatibilityError(f"{origin} stable source must select exactly one tag")
    if verification["mode"] != "Tag" or secret_reference["name"] != "k8s-stack-release-trust":
        raise CompatibilityError(f"{origin} requires Tag verification and k8s-stack-release-trust")
    tag = reference["tag"]
    parse_tag(tag, field=f"{origin} platform pin")
    mode = annotations[ADOPTION_MODE_ANNOTATION]
    if not isinstance(mode, str) or mode not in ADOPTION_MODES:
        raise CompatibilityError(f"{origin} adoption-mode must be fresh-install or upgrade")
    if annotations[ADOPTION_TARGET_ANNOTATION] != tag:
        raise CompatibilityError(f"{origin} adoption-target must equal the selected tag")
    promoted = annotations.get(PROMOTED_FROM_ALPHA_ANNOTATION)
    if PROMOTED_FROM_ALPHA_ANNOTATION in annotations and (
        not isinstance(promoted, str) or COMMIT_SHA_PATTERN.fullmatch(promoted) is None
    ):
        raise CompatibilityError(f"{origin} promoted-from-alpha must be a full 40-hex commit SHA")
    return PlatformSource("stable", "tag", tag, mode, promoted)


def parse_kustomization(content: str, *, origin: str, resources: list[str]) -> None:
    try:
        document = yaml.safe_load(content)
    except (ValueError, RecursionError, yaml.YAMLError) as error:
        raise CompatibilityError(f"cannot parse {origin}; details withheld") from error
    if document != {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": resources,
    }:
        raise CompatibilityError(
            f"{origin} must remain transform-free with the canonical resources"
        )


def parse_flux_sync(content: str, *, origin: str) -> list[dict[str, Any]]:
    try:
        documents = [document for document in yaml.safe_load_all(content) if document]
        if len(documents) != 2:
            raise CompatibilityError(f"{origin} must contain exactly two resources")
        source_url = documents[0]["spec"]["url"]
    except (TypeError, KeyError, ValueError, RecursionError, yaml.YAMLError) as error:
        raise CompatibilityError(f"cannot parse {origin}; details withheld") from error
    if not isinstance(source_url, str) or CLIENT_SOURCE_URL_PATTERN.fullmatch(source_url) is None:
        raise CompatibilityError(f"{origin} must use the canonical client source URL")
    expected = [
        {
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "GitRepository",
            "metadata": {"name": "flux-system", "namespace": "flux-system"},
            "spec": {
                "interval": "1m0s",
                "ref": {"branch": "main"},
                "secretRef": {"name": "flux-system"},
                "url": source_url,
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
    if documents != expected:
        raise CompatibilityError(f"{origin} must retain the canonical bootstrap spec")
    return documents


def read_control_plane_contract(
    root: Path,
    revision: str,
    revision_reader: Callable[[Path, str, Path], str],
) -> tuple[list[dict[str, Any]], str]:
    for path, resources in (
        (CLUSTER_KUSTOMIZATION_PATH, CLUSTER_RESOURCES),
        (FLUX_KUSTOMIZATION_PATH, FLUX_RESOURCES),
    ):
        parse_kustomization(
            revision_reader(root, revision, path),
            origin="composition Kustomization",
            resources=resources,
        )
    sync = parse_flux_sync(
        revision_reader(root, revision, FLUX_SYNC_PATH), origin="Flux bootstrap sync"
    )
    return sync, revision_reader(root, revision, FLUX_COMPONENTS_PATH)


def read_at_revision(root: Path, revision: str, path: Path) -> str:
    """Read only an exact, already-fetched commit, with a bounded review size."""
    if SHA_PATTERN.fullmatch(revision) is None:
        raise CompatibilityError("revision must be a full hexadecimal Git object ID")
    object_name = f"{revision}:{path.as_posix()}"
    try:
        size = int(run_command(["git", "cat-file", "-s", object_name], cwd=root).strip())
    except ValueError as error:
        raise CompatibilityError("cannot determine revision file size") from error
    if not 0 <= size <= MAX_REVISION_FILE_BYTES:
        raise CompatibilityError("revision file exceeds the review size limit")
    return run_command(["git", "show", object_name], cwd=root)


def source_ignore_paths(root: Path, revision: str) -> list[str]:
    if SHA_PATTERN.fullmatch(revision) is None:
        raise CompatibilityError("revision must be a full hexadecimal Git object ID")
    output = run_command(["git", "ls-tree", "-r", "-z", "--name-only", revision], cwd=root)
    return [path for path in output.split("\0") if path.rsplit("/", 1)[-1] == ".sourceignore"]


def fetch_pull_request_refs(
    root: Path, pull_request_number: int, base_sha: str, head_sha: str, merge_sha: str
) -> None:
    """Fetch PR data, never its executable code; bind the exact test-merge tuple."""
    if pull_request_number < 1:
        raise CompatibilityError("pull-request number must be a positive integer")
    for sha in (base_sha, head_sha, merge_sha):
        if SHA_PATTERN.fullmatch(sha) is None:
            raise CompatibilityError("PR SHAs must be full hexadecimal Git object IDs")
    head_ref = "refs/platform-compatibility/pull-request-head"
    merge_ref = "refs/platform-compatibility/pull-request-merge"
    # Depth two preserves both merge parents, even in an initially shallow checkout.
    run_command(
        [
            "git",
            "fetch",
            "--quiet",
            "--no-tags",
            "--depth=2",
            "origin",
            f"+refs/pull/{pull_request_number}/head:{head_ref}",
            f"+refs/pull/{pull_request_number}/merge:{merge_ref}",
        ],
        cwd=root,
    )
    fetched_head = run_command(["git", "rev-parse", head_ref], cwd=root).strip()
    fetched_merge = run_command(["git", "rev-parse", merge_ref], cwd=root).strip()
    if fetched_head != head_sha or fetched_merge != merge_sha:
        raise CompatibilityError(
            "fetched pull-request head or merge does not match the event/API SHA"
        )
    parents = run_command(["git", "rev-list", "--parents", "-n", "1", merge_ref], cwd=root).split()
    if parents != [merge_sha, base_sha, head_sha]:
        raise CompatibilityError(
            "pull-request test merge must have the exact current base and head parents"
        )


def fetch_base_revision(selector: str, revision: str) -> str:
    if (selector, revision) == ("branch", "main"):
        reference = "refs/heads/main"
    elif selector == "commit" and COMMIT_SHA_PATTERN.fullmatch(revision):
        return revision
    elif selector == "tag":
        parse_tag(revision, field="Base tag revision")
        reference = f"refs/tags/{revision}^{{}}"
    else:
        raise CompatibilityError("unsupported Base selector")
    output = run_command(["git", "ls-remote", PLATFORM_SOURCE_URL, reference]).split()
    if len(output) != 2 or COMMIT_SHA_PATTERN.fullmatch(output[0]) is None:
        raise CompatibilityError("cannot resolve Base revision")
    return output[0]


def is_base_ancestor(ancestor: str, descendant: str) -> bool:
    for revision in (ancestor, descendant):
        if COMMIT_SHA_PATTERN.fullmatch(revision) is None:
            raise CompatibilityError("ancestry revisions must be full 40-hex commit SHAs")
    with tempfile.TemporaryDirectory(prefix="platform-ancestry-") as temporary:
        repository = Path(temporary) / "base"
        repository.mkdir()
        run_command(["git", "init", "--quiet"], cwd=repository)
        run_command(["git", "remote", "add", "origin", PLATFORM_SOURCE_URL], cwd=repository)
        run_command(
            ["git", "fetch", "--quiet", "--no-tags", "origin", ancestor, descendant], cwd=repository
        )
        try:
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", ancestor, descendant],
                cwd=repository,
                check=False,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CompatibilityError("Base ancestry comparison unavailable or timed out") from error
        if result.returncode not in (0, 1):
            raise CompatibilityError("cannot compare Base revisions; details withheld")
        return result.returncode == 0


def fetch_and_verify_tag(tag: str, expected_fingerprint: str) -> VerifiedTag:
    """Authenticate the tag before reading its manifest or optional migration notes."""
    parse_tag(tag, field="release tag")
    if FINGERPRINT_PATTERN.fullmatch(expected_fingerprint) is None:
        raise CompatibilityError("trusted signer fingerprint is invalid")
    with tempfile.TemporaryDirectory(prefix="platform-release-") as temporary:
        repository = Path(temporary) / "base"
        repository.mkdir()
        run_command(["git", "init", "--quiet"], cwd=repository)
        run_command(["git", "remote", "add", "origin", PLATFORM_SOURCE_URL], cwd=repository)
        tag_ref = f"refs/tags/{tag}"
        run_command(
            ["git", "fetch", "--quiet", "--no-tags", "--depth=1", "origin", f"{tag_ref}:{tag_ref}"],
            cwd=repository,
        )
        if run_command(["git", "cat-file", "-t", tag_ref], cwd=repository).strip() != "tag":
            raise CompatibilityError("release must use an annotated tag")
        public_key = run_command(
            ["git", "show", f"{tag_ref}:{TRUST_KEY_PATH}"], cwd=repository
        ).strip()
        key_parts = public_key.split()
        if len(key_parts) < 2 or key_parts[0] != "ssh-ed25519":
            raise CompatibilityError("release contains an invalid platform release public key")
        key_file = Path(temporary) / "platform-release.sshpub"
        key_file.write_text(f"{public_key}\n", encoding="utf-8")
        fingerprint = run_command(["ssh-keygen", "-lf", str(key_file), "-E", "sha256"]).split()
        if len(fingerprint) < 2 or fingerprint[1] != expected_fingerprint:
            raise CompatibilityError(
                "release signer does not match the out-of-band trusted fingerprint"
            )
        allowed_signers = Path(temporary) / "allowed_signers"
        allowed_signers.write_text(
            f"platform-release {key_parts[0]} {key_parts[1]}\n", encoding="utf-8"
        )
        run_command(
            ["git", "-c", f"gpg.ssh.allowedSignersFile={allowed_signers}", "verify-tag", tag_ref],
            cwd=repository,
        )
        manifest = run_command(["git", "show", f"{tag_ref}:release/manifest.yaml"], cwd=repository)
        migration_path = f"release/migrations/{tag}.md"
        migration = (
            run_command(["git", "show", f"{tag_ref}:{migration_path}"], cwd=repository)
            if run_command(
                ["git", "ls-tree", "--name-only", tag_ref, "--", migration_path], cwd=repository
            ).strip()
            else ""
        )
        commit = run_command(["git", "rev-parse", f"{tag_ref}^{{commit}}"], cwd=repository).strip()
        if COMMIT_SHA_PATTERN.fullmatch(commit) is None:
            raise CompatibilityError("release tag did not peel to a full commit SHA")
        return VerifiedTag(manifest, migration, commit)


def fetch_release(tag: str, token: str | None) -> dict[str, Any]:
    parse_tag(tag, field="release tag")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "neurwerk-client-platform-compatibility",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{PLATFORM_RELEASE_API}{quote(tag, safe='')}", headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise CompatibilityError(f"GitHub Release lookup failed with HTTP {error.code}") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise CompatibilityError("GitHub Release lookup failed; details withheld") from error
    if not isinstance(payload, dict):
        raise CompatibilityError("GitHub Release lookup returned invalid data")
    return payload


def migration_sections(migration: str, tag: str) -> dict[str, str]:
    if migration.startswith("# Platform ") and not migration.startswith(f"# Platform {tag}\n"):
        raise CompatibilityError("migration title must agree with the tag")
    matches = list(re.finditer(r"^## (.+?)\s*$", migration, flags=re.MULTILINE))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(migration)
        heading = match.group(1)
        if heading in sections:
            raise CompatibilityError("migration contains duplicate headings")
        sections[heading] = migration[match.end() : end].strip()
    return sections


def parse_support_contract(support: str) -> tuple[set[str] | str | None, str | None]:
    downgrade_matches = re.findall(r"^- Downgrade: (.+)\.$", support, flags=re.MULTILINE)
    if len(downgrade_matches) > 1 or (
        not downgrade_matches and re.search(r"^- Downgrade:", support, re.MULTILINE)
    ):
        raise CompatibilityError("migration Support must contain exactly one downgrade declaration")
    try:
        downgrade = (
            COMPATIBILITY_DISPLAY_VALUES[downgrade_matches[0]] if downgrade_matches else None
        )
    except KeyError as error:
        raise CompatibilityError("migration downgrade must be Supported or Unsupported") from error
    stable_matches = re.findall(r"^- Stable upgrades: (.+)\.$", support, flags=re.MULTILINE)
    source_matches = re.findall(
        r"^- Supported source versions: (.+)\.$", support, flags=re.MULTILINE
    )
    if (
        not stable_matches
        and not source_matches
        and not re.search(r"^- (Stable upgrades|Supported source versions):", support, re.MULTILINE)
    ):
        return None, downgrade
    if len(stable_matches) + len(source_matches) != 1:
        raise CompatibilityError(
            "migration Support must contain exactly one stable upgrade declaration"
        )
    if stable_matches:
        try:
            return STABLE_UPGRADE_DISPLAY_VALUES[stable_matches[0]], downgrade
        except KeyError as error:
            raise CompatibilityError(
                "migration stable upgrades must be Supported or Fresh installation only"
            ) from error
    sources = []
    if source_matches[0] != "None":
        for item in source_matches[0].split(","):
            match = re.fullmatch(r"`([^`]+)`", item.strip())
            if match is None:
                raise CompatibilityError(
                    "migration supported sources must be None or exact tags in backticks"
                )
            tag = match.group(1)
            parse_tag(tag, field="migration supported source version")
            sources.append(tag)
    if len(sources) != len(set(sources)):
        raise CompatibilityError("migration supported source versions contain duplicates")
    return set(sources), downgrade


def parse_recovery_contract(recovery: str) -> str | None:
    matches = re.findall(r"^Recovery classification: (.+)\.$", recovery, flags=re.MULTILINE)
    if not matches and not re.search(r"^Recovery classification:", recovery, re.MULTILINE):
        return None
    if len(matches) != 1:
        raise CompatibilityError(
            "migration Recovery must contain exactly one recovery classification declaration"
        )
    try:
        return RECOVERY_DISPLAY_VALUES[matches[0]]
    except KeyError as error:
        raise CompatibilityError("migration recovery classification is not supported") from error


def parse_alpha_source_revisions(support: str) -> set[str] | None:
    prefix = "- Supported alpha source revisions: "
    declarations = [
        line.removeprefix(prefix) for line in support.splitlines() if line.startswith(prefix)
    ]
    if not declarations:
        return None
    if len(declarations) != 1 or not declarations[0].endswith("."):
        raise CompatibilityError(
            "migration Support must contain exactly one alpha source revisions declaration"
        )
    value = declarations[0][:-1]
    if value == "None":
        return set()
    revisions = []
    for item in value.split(","):
        match = re.fullmatch(r"`([0-9a-f]{40})`", item.strip())
        if match is None:
            raise CompatibilityError(
                "migration alpha sources must be None or full 40-hex SHAs in backticks"
            )
        revisions.append(match.group(1))
    if len(revisions) != len(set(revisions)):
        raise CompatibilityError("migration alpha source revisions contain duplicates")
    return set(revisions)


def validate_release_contract(
    old_tag: str,
    new_tag: str,
    manifest_text: str,
    migration: str,
    release: dict[str, Any],
    adoption_mode: str,
    expected_fingerprint: str,
    *,
    validate_transition: bool = True,
) -> None:
    new_version = parse_tag(new_tag, field="proposed platform pin")
    if validate_transition and new_version <= parse_tag(old_tag, field="base platform pin"):
        raise CompatibilityError("platform transition is unchanged or a downgrade")
    try:
        manifest = yaml.safe_load(manifest_text)
        spec = manifest["spec"]
        compatibility, trust = spec["compatibility"], spec["trust"]
        metadata = manifest["metadata"]
        if not all(
            isinstance(value, dict) for value in (manifest, spec, compatibility, trust, metadata)
        ):
            raise CompatibilityError("release manifest must use mappings")
    except (TypeError, KeyError, ValueError, RecursionError, yaml.YAMLError) as error:
        raise CompatibilityError("release manifest is malformed; details withheld") from error
    if (
        manifest.get("apiVersion") != "platform.neurwerk.com/v1alpha1"
        or manifest.get("kind") != "PlatformRelease"
    ):
        raise CompatibilityError("manifest API or kind is not supported")
    if metadata.get("name") != new_tag or str(spec.get("version")) != new_tag.removeprefix("v"):
        raise CompatibilityError(
            "manifest metadata.name or spec.version does not agree with the tag"
        )
    if trust.get("algorithm") != "ssh-ed25519" or trust.get("fingerprint") != expected_fingerprint:
        raise CompatibilityError(
            "manifest signer does not match the out-of-band trusted fingerprint and algorithm"
        )
    legacy = "upgradesFrom" in compatibility
    if legacy:
        if new_tag not in LEGACY_UPGRADES_FROM_TARGETS:
            raise CompatibilityError(
                "manifest upgradesFrom is legacy and allowed only for v0.1.0 and v0.1.1"
            )
        if "stableUpgrade" in compatibility:
            raise CompatibilityError("manifest must not combine upgradesFrom and stableUpgrade")
        sources = compatibility["upgradesFrom"]
        if not isinstance(sources, list):
            raise CompatibilityError("manifest upgradesFrom must be a list of tags")
        for version in sources:
            parse_tag(version, field="manifest upgradesFrom entry")
        stable_support = set(sources)
    else:
        stable_support = compatibility.get("stableUpgrade")
        if stable_support not in tuple(STABLE_UPGRADE_DISPLAY_VALUES.values()):
            raise CompatibilityError(
                "manifest stableUpgrade must be supported or fresh-install-only"
            )
    downgrade, recovery = compatibility.get("downgrade"), compatibility.get("recovery")
    if downgrade not in tuple(COMPATIBILITY_DISPLAY_VALUES.values()):
        raise CompatibilityError("manifest downgrade must be supported or unsupported")
    if recovery not in tuple(RECOVERY_DISPLAY_VALUES.values()):
        raise CompatibilityError("manifest recovery classification is not supported")
    if release.get("tag_name") != new_tag:
        raise CompatibilityError("GitHub Release tag does not agree with the proposed pin")
    if (
        release.get("draft") is not False
        or release.get("prerelease") is not False
        or not release.get("published_at")
    ):
        raise CompatibilityError("target GitHub Release must be published, non-draft, and full")
    migration_sections(migration, new_tag)
    migration_support, migration_downgrade = parse_support_contract(migration)
    migration_recovery = parse_recovery_contract(migration)
    for name, declared, actual in (
        ("stable upgrade support", migration_support, stable_support),
        ("downgrade support", migration_downgrade, downgrade),
        ("recovery classification", migration_recovery, recovery),
    ):
        if declared is not None and declared != actual:
            raise CompatibilityError(f"migration {name} disagrees with the manifest")
    if downgrade != "unsupported":
        raise CompatibilityError("manifest must explicitly mark downgrade as unsupported")
    if adoption_mode not in ADOPTION_MODES:
        raise CompatibilityError("platform adoption mode must be fresh-install or upgrade")
    if validate_transition and adoption_mode == "upgrade":
        if legacy and old_tag not in stable_support:
            raise CompatibilityError("target does not support an upgrade from the current tag")
        if not legacy and stable_support != "supported":
            raise CompatibilityError("target supports fresh installation only, not stable upgrades")


def validate_alpha_promotion_contract(
    source_revision: str,
    target: PlatformSource,
    verified_tag: VerifiedTag,
    release: dict[str, Any],
    expected_fingerprint: str,
    *,
    forward: bool,
) -> None:
    validate_release_contract(
        target.revision,
        target.revision,
        verified_tag.manifest,
        verified_tag.migration,
        release,
        target.adoption_mode or "",
        expected_fingerprint,
        validate_transition=False,
    )
    compatibility = yaml.safe_load(verified_tag.manifest)["spec"]["compatibility"]
    manifest_revisions = None
    if "upgradesFromAlphaRevisions" in compatibility:
        revisions = compatibility["upgradesFromAlphaRevisions"]
        if not isinstance(revisions, list) or not all(
            isinstance(revision, str) and COMMIT_SHA_PATTERN.fullmatch(revision)
            for revision in revisions
        ):
            raise CompatibilityError(
                "manifest upgradesFromAlphaRevisions must be a list of full 40-hex SHAs"
            )
        if len(revisions) != len(set(revisions)):
            raise CompatibilityError("manifest upgradesFromAlphaRevisions contains duplicates")
        manifest_revisions = set(revisions)
    migration_revisions = parse_alpha_source_revisions(verified_tag.migration)
    if migration_revisions is not None and migration_revisions != manifest_revisions:
        raise CompatibilityError(
            "migration alpha source revisions must exactly equal manifest upgradesFromAlphaRevisions"
        )
    if target.adoption_mode == "fresh-install" or not forward:
        return
    if manifest_revisions is None:
        raise CompatibilityError(
            "forward alpha upgrade requires manifest upgradesFromAlphaRevisions"
        )
    if source_revision not in manifest_revisions:
        raise CompatibilityError(
            "target manifest does not list the exact promoted alpha source revision"
        )


def run_check(
    root: Path,
    base_sha: str,
    proposed_sha: str,
    expected_fingerprint: str,
    github_token: str | None,
    *,
    classify_only: bool = False,
    allow_alpha: bool = False,
    expected_old_alpha_commit: str | None = None,
    expected_new_alpha_commit: str | None = None,
    revision_reader: Callable[[Path, str, Path], str] = read_at_revision,
    source_ignore_finder: Callable[[Path, str], list[str]] = source_ignore_paths,
    tag_fetcher: Callable[[str, str], VerifiedTag] = fetch_and_verify_tag,
    release_fetcher: Callable[[str, str | None], dict[str, Any]] = fetch_release,
    base_revision_fetcher: Callable[[str, str], str] = fetch_base_revision,
    ancestor_checker: Callable[[str, str], bool] = is_base_ancestor,
) -> CheckResult:
    for revision in (base_sha, proposed_sha):
        if SHA_PATTERN.fullmatch(revision) is None:
            raise CompatibilityError("client revisions must be full hexadecimal Git object IDs")
        if source_ignore_finder(root, revision):
            raise CompatibilityError(
                "client revision contains prohibited .sourceignore files; paths withheld"
            )
    old_source = parse_platform_source(
        revision_reader(root, base_sha, PLATFORM_SOURCE_PATH),
        origin="base source",
        allow_alpha=allow_alpha,
    )
    new_source = parse_platform_source(
        revision_reader(root, proposed_sha, PLATFORM_SOURCE_PATH),
        origin="proposed source",
        allow_alpha=allow_alpha,
    )
    old_controls = read_control_plane_contract(root, base_sha, revision_reader)
    new_controls = read_control_plane_contract(root, proposed_sha, revision_reader)
    if old_controls != new_controls:
        raise CompatibilityError(
            "Flux bootstrap controls may not change in a platform adoption pull request"
        )
    if (
        old_source.channel == "alpha"
        and old_source.selector == "branch"
        and new_source.channel == "stable"
    ):
        raise CompatibilityError(
            "alpha branch cannot transition directly to stable; freeze the source and reconcile it first"
        )
    resolved_revisions: dict[tuple[str, str], str] = {}

    def validated_commit(value: str) -> str:
        if not isinstance(value, str) or COMMIT_SHA_PATTERN.fullmatch(value) is None:
            raise CompatibilityError("Base revision must resolve to a full 40-hex commit SHA")
        return value

    def resolve(selector: str, revision: str) -> str:
        key = selector, revision
        if key not in resolved_revisions:
            resolved_revisions[key] = validated_commit(base_revision_fetcher(selector, revision))
        return resolved_revisions[key]

    def resolve_alpha(source: PlatformSource) -> str | None:
        if source.channel != "alpha":
            return None
        if source.selector == "branch":
            return resolve("branch", "main")
        pinned, main = resolve("commit", source.revision), resolve("branch", "main")
        if pinned != main and not ancestor_checker(pinned, main):
            raise CompatibilityError(
                "alpha commit must be equal to or an ancestor of protected Base main"
            )
        return pinned

    old_alpha, new_alpha = resolve_alpha(old_source), resolve_alpha(new_source)
    for name, expected, actual in (
        ("old", expected_old_alpha_commit, old_alpha),
        ("new", expected_new_alpha_commit, new_alpha),
    ):
        if expected is not None:
            validated_commit(expected)
            if actual != expected:
                raise CompatibilityError(
                    f"resolved {name} alpha commit changed since classification"
                )
    changed = old_source != new_source

    if old_source.channel == new_source.channel == "alpha":
        if (
            old_source.selector == "branch"
            and new_source.selector == "commit"
            and old_alpha != new_alpha
        ):
            raise CompatibilityError(
                "alpha freeze commit must exactly equal the resolved protected Base main revision"
            )
        if (
            old_source.selector == new_source.selector == "commit"
            and old_alpha != new_alpha
            and not ancestor_checker(old_alpha, new_alpha)
        ):
            raise CompatibilityError("alpha commit source may not move backward or diverge")
    else:
        # Every transition involving stable, including an unchanged pin, needs trusted signer configuration.
        if FINGERPRINT_PATTERN.fullmatch(expected_fingerprint) is None:
            raise CompatibilityError(
                "PLATFORM_RELEASE_SIGNER_FINGERPRINT is missing or invalid; configure the trusted SHA256 fingerprint"
            )
        if old_source.channel == new_source.channel == "stable":
            if old_source.revision == new_source.revision:
                if changed:
                    raise CompatibilityError(
                        "stable source annotations may change only with the exact platform tag"
                    )
            else:
                if new_source.promoted_from_alpha is not None:
                    raise CompatibilityError(
                        "promoted-from-alpha is valid only for alpha-to-stable transitions"
                    )
                if parse_tag(new_source.revision, field="proposed pin") < parse_tag(
                    old_source.revision, field="base pin"
                ):
                    raise CompatibilityError("platform downgrade is prohibited")
                if not classify_only:
                    verified = tag_fetcher(new_source.revision, expected_fingerprint)
                    validate_release_contract(
                        old_source.revision,
                        new_source.revision,
                        verified.manifest,
                        verified.migration,
                        release_fetcher(new_source.revision, github_token),
                        new_source.adoption_mode or "",
                        expected_fingerprint,
                    )
        elif new_source.channel == "alpha":
            baseline = validated_commit(
                tag_fetcher(old_source.revision, expected_fingerprint).commit
            )
            if baseline != new_alpha and not ancestor_checker(baseline, new_alpha):
                raise CompatibilityError(
                    "selected alpha revision is behind or divergent from the authenticated stable release"
                )
        else:
            if new_source.promoted_from_alpha != old_alpha:
                raise CompatibilityError(
                    "promoted-from-alpha must equal the exact observed alpha revision"
                )
            if classify_only:
                target_commit = resolve(new_source.selector, new_source.revision)
            else:
                verified = tag_fetcher(new_source.revision, expected_fingerprint)
                target_commit = validated_commit(verified.commit)
            exact = target_commit == old_alpha
            if not exact and not ancestor_checker(old_alpha, target_commit):
                raise CompatibilityError(
                    "stable target is behind or divergent from the observed alpha revision"
                )
            if not classify_only:
                validate_alpha_promotion_contract(
                    old_alpha,
                    new_source,
                    verified,
                    release_fetcher(new_source.revision, github_token),
                    expected_fingerprint,
                    forward=not exact,
                )
    return CheckResult(
        old_source, new_source, old_alpha, new_alpha, changed, changed and not classify_only
    )


def main(argv: list[str] | None = None) -> int:
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(2, "::error::invalid compatibility CLI arguments; details withheld\n")

    parser = Parser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--merge-sha")
    parser.add_argument("--pull-request-number", type=int)
    parser.add_argument("--classify-only", action="store_true")
    parser.add_argument(
        "--allow-alpha", action="store_true", help="Trusted workflow opt-in to alpha transitions"
    )
    parser.add_argument("--expected-old-alpha-commit")
    parser.add_argument("--expected-new-alpha-commit")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.pull_request_number is not None:
            if args.merge_sha is None:
                raise CompatibilityError(
                    "merge SHA is required when a pull-request number is supplied"
                )
            fetch_pull_request_refs(
                root, args.pull_request_number, args.base_sha, args.head_sha, args.merge_sha
            )
        result = run_check(
            root,
            args.base_sha,
            args.merge_sha or args.head_sha,
            os.environ.get("PLATFORM_RELEASE_SIGNER_FINGERPRINT", ""),
            os.environ.get("GITHUB_TOKEN"),
            classify_only=args.classify_only,
            allow_alpha=args.allow_alpha,
            expected_old_alpha_commit=args.expected_old_alpha_commit or None,
            expected_new_alpha_commit=args.expected_new_alpha_commit or None,
        )
        if args.github_output is not None:
            with args.github_output.open("a", encoding="utf-8") as output:
                for prefix, source in (("old", result.old_source), ("new", result.new_source)):
                    output.write(f"{prefix}_source_channel={source.channel}\n")
                    output.write(f"{prefix}_source_selector={source.selector}\n")
                    output.write(f"{prefix}_source_revision={source.revision}\n")
                output.write(f"old_alpha_commit={result.old_alpha_commit or ''}\n")
                output.write(f"new_alpha_commit={result.new_alpha_commit or ''}\n")
                output.write(
                    f"adoption_mode={result.new_source.adoption_mode or 'not-applicable'}\n"
                )
                output.write(f"platform_source_changed={str(result.changed).lower()}\n")
                output.write(f"platform_verified={str(result.verified).lower()}\n")
    except CompatibilityError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, OverflowError, RecursionError):
        print(
            "::error::cannot read or validate compatibility inputs; details withheld",
            file=sys.stderr,
        )
        return 1
    old = f"{result.old_source.channel} {result.old_source.selector}:{result.old_source.revision}"
    new = f"{result.new_source.channel} {result.new_source.selector}:{result.new_source.revision}"
    if result.changed and result.verified:
        print(f"verified compatible platform transition {old} -> {new}")
    elif result.changed:
        print(f"platform source change classified: {old} -> {new}")
    else:
        print(f"platform source unchanged at {new}; release verification skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
