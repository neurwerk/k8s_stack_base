#!/usr/bin/env python3
"""Generate and validate the aggregate platform release manifest."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "release/config.yaml"
MANIFEST_PATH = ROOT / "release/manifest.yaml"
VERSION_PATH = ROOT / "VERSION"
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
PUBLIC_KEY_PATH = ROOT / "release/trust/platform-release.sshpub"
LINT_VALUES_PATH = ROOT / "tests/validation/helm-lint-values.yaml"
SCHEMA_PATH = ROOT / "release/manifest.schema.json"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
RELEASE_TAG = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
IMAGE_TAG = re.compile(r"^(?P<repository>.+):(?P<tag>[^/:]+)$")
REPOSITORY_URL = "https://github.com/neurwerk/k8s_stack_base"
UNPUBLISHED_BASELINE_VERSION = "0.0.0"
BOOTSTRAP_RELEASE_VERSION = "0.1.0"
BOOTSTRAP_RELEASE_TAG = f"v{BOOTSTRAP_RELEASE_VERSION}"
MINIMUM_PROVENANCE_RELEASE = BOOTSTRAP_RELEASE_VERSION
RECOVERY_ACTIONS = (
    "configuration-revert",
    "forward-fix",
    "component-native-restore",
    "replacement-restore",
)


class ReleaseError(RuntimeError):
    """Raised when release evidence is incomplete or inconsistent."""


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        try:
            display = path.relative_to(ROOT)
        except ValueError:
            display = path
        raise ReleaseError(f"{display} must contain a YAML mapping")
    return data


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*arguments: str, repository: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReleaseError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout.strip()


def git_is_ancestor(ancestor: str, descendant: str, repository: Path = ROOT) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReleaseError(f"git merge-base --is-ancestor failed: {detail}")
    return result.returncode == 0


def semver_tuple(value: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(value)
    if match is None:
        raise ReleaseError(f"invalid SemVer version: {value!r}")
    return tuple(int(part) for part in match.groups())


def validate_release_date(value: str) -> None:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ReleaseError("release-date must use strict YYYY-MM-DD format")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ReleaseError("release-date must be a valid calendar date") from exc
    if parsed.isoformat() != value:
        raise ReleaseError("release-date must use strict YYYY-MM-DD format")


def validate_previous_tag_at_included_through(
    previous_tag: str,
    included_through: str,
    repository: Path = ROOT,
) -> None:
    included_version = git(
        "show", f"{included_through}:VERSION", repository=repository
    ).strip()
    if SEMVER.fullmatch(included_version) is None:
        raise ReleaseError("VERSION at provenance includedThrough is not strict SemVer")
    if previous_tag != f"v{included_version}":
        raise ReleaseError(
            "release provenance previousTag does not match VERSION at includedThrough"
        )


def provenance_from_git(previous_tag: str, included_through: str | None = None) -> dict[str, Any]:
    if RELEASE_TAG.fullmatch(previous_tag) is None:
        raise ReleaseError(f"invalid previous release tag: {previous_tag!r}")
    previous_commit = git("rev-parse", "--verify", f"{previous_tag}^{{commit}}")
    included = git("rev-parse", included_through or "HEAD")
    if GIT_COMMIT.fullmatch(included) is None:
        raise ReleaseError("includedThrough must resolve to a full Git commit")
    if not git_is_ancestor(previous_commit, included):
        raise ReleaseError(f"{previous_tag} is not an ancestor of {included}")
    commits = git("rev-list", "--reverse", f"{previous_tag}..{included}").splitlines()
    if not commits:
        raise ReleaseError(f"no commits are included after {previous_tag}")
    return {
        "previousTag": previous_tag,
        "includedThrough": included,
        "commits": commits,
        "compareUrl": f"{REPOSITORY_URL}/compare/{previous_tag}...{included}",
    }


def repository_tags(repository: Path = ROOT) -> list[str]:
    """Return every tag in the repository in stable order."""
    return sorted(git("tag", "--list", repository=repository).splitlines())


def bootstrap_provenance_from_git(
    included_through: str | None = None, repository: Path = ROOT
) -> dict[str, Any]:
    """Record the complete reachable history for the first platform release."""
    included = git("rev-parse", included_through or "HEAD", repository=repository)
    if GIT_COMMIT.fullmatch(included) is None:
        raise ReleaseError("includedThrough must resolve to a full Git commit")
    commits = git("rev-list", "--reverse", included, repository=repository).splitlines()
    if not commits:
        raise ReleaseError("bootstrap provenance contains no reachable commits")
    return {
        "bootstrap": True,
        "includedThrough": included,
        "commits": commits,
        "historyUrl": f"{REPOSITORY_URL}/commits/{included}",
    }


def provenance_mode(provenance: Any) -> str:
    """Return the validated provenance variant name."""
    if not isinstance(provenance, dict):
        raise ReleaseError("release provenance must be an object")
    if provenance.get("bootstrap") is True:
        return "bootstrap"
    if "previousTag" in provenance:
        return "predecessor"
    raise ReleaseError("release provenance has no recognized mode")


def release_tag_for_commit(commit: str, repository: Path = ROOT) -> str:
    resolved = git("rev-parse", commit, repository=repository)
    matches: list[str] = []
    output = git(
        "for-each-ref",
        "--format=%(refname:short)\t%(objecttype)\t%(objectname)\t%(*objectname)",
        "refs/tags",
        repository=repository,
    )
    for line in output.splitlines():
        tag, object_type, object_name, peeled_name = line.split("\t")
        target = peeled_name if object_type == "tag" else object_name
        if target == resolved and RELEASE_TAG.fullmatch(tag):
            matches.append(tag)
    if len(matches) != 1:
        raise ReleaseError(f"expected exactly one release tag for {resolved}, found {matches}")
    return matches[0]


def latest_release_tag(repository: Path = ROOT) -> str:
    tags = git("tag", "--list", "v*", repository=repository).splitlines()
    reachable = [
        tag
        for tag in tags
        if RELEASE_TAG.fullmatch(tag)
        and git_is_ancestor(f"{tag}^{{commit}}", "HEAD", repository=repository)
    ]
    if not reachable:
        raise ReleaseError("no reachable signed release tag exists")
    return max(reachable, key=lambda tag: semver_tuple(tag.removeprefix("v")))


def verify_release_tag_signature(tag: str, expected_ref: str, repository: Path = ROOT) -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/verify_release_tag.sh"), tag, expected_ref],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReleaseError(f"release tag signature verification failed for {tag}: {detail}")


def public_key_fingerprint() -> tuple[str, str, str]:
    fields = PUBLIC_KEY_PATH.read_text().strip().split()
    if len(fields) < 2:
        raise ReleaseError("release public key is malformed")
    algorithm, encoded_key = fields[:2]
    comment = fields[2] if len(fields) > 2 else ""
    try:
        key_blob = base64.b64decode(encoded_key, validate=True)
    except ValueError as exc:
        raise ReleaseError("release public key is malformed") from exc
    fingerprint = base64.b64encode(hashlib.sha256(key_blob).digest()).decode().rstrip("=")
    return algorithm, f"SHA256:{fingerprint}", comment


def chart_inventory() -> list[dict[str, Any]]:
    charts: list[dict[str, Any]] = []
    for chart_path in sorted((ROOT / "charts").glob("**/Chart.yaml")):
        metadata = load_yaml(chart_path)
        chart_dir = chart_path.parent
        item: dict[str, Any] = {
            "path": chart_dir.relative_to(ROOT).as_posix(),
            "name": str(metadata["name"]),
            "version": str(metadata["version"]),
        }
        if "appVersion" in metadata:
            item["appVersion"] = str(metadata["appVersion"])

        lock_path = chart_dir / "Chart.lock"
        if lock_path.exists():
            lock = load_yaml(lock_path)
            archives = [
                {
                    "path": archive.relative_to(ROOT).as_posix(),
                    "sha256": sha256(archive),
                }
                for archive in sorted((chart_dir / "charts").glob("*.tgz"))
            ]
            item["dependencyLock"] = {
                "digest": str(lock["digest"]),
                "dependencies": lock.get("dependencies", []),
                "archives": archives,
            }
        charts.append(item)
    return charts


def helm_release_inventory() -> list[dict[str, str]]:
    releases: list[dict[str, str]] = []
    for path in sorted((ROOT / "releases").glob("**/*.yaml")):
        for document in yaml.safe_load_all(path.read_text()):
            if not isinstance(document, dict) or document.get("kind") != "HelmRelease":
                continue
            metadata = document["metadata"]
            chart = document["spec"]["chart"]["spec"]["chart"]
            releases.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "namespace": str(metadata["namespace"]),
                    "name": str(metadata["name"]),
                    "chart": str(chart),
                }
            )
    return releases


def toolchain_inventory() -> dict[str, str]:
    tools: dict[str, str] = {}
    for line in (ROOT / ".tool-versions").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, version = line.split(maxsplit=1)
        tools[name] = version
    return tools


def compose_image(value: dict[str, Any]) -> str | None:
    repository = value.get("repository")
    tag = value.get("tag")
    if not isinstance(repository, str) or not repository or tag in (None, ""):
        return None
    registry = value.get("registry")
    prefix = f"{registry.rstrip('/')}/" if isinstance(registry, str) and registry else ""
    return f"{prefix}{repository}:{tag}"


def compose_repository_images(value: dict[str, Any]) -> Iterable[str]:
    repositories = value.get("repositories")
    tag = value.get("tag")
    if not isinstance(repositories, dict) or tag in (None, ""):
        return
    registry = value.get("registry")
    prefix = f"{registry.rstrip('/')}/" if isinstance(registry, str) and registry else ""
    for repository in repositories.values():
        if isinstance(repository, str) and repository:
            yield f"{prefix}{repository}:{tag}"


def image_values(value: Any, key_path: tuple[str, ...] = ()) -> Iterable[str]:
    if isinstance(value, dict):
        image = compose_image(value)
        if image and any("image" in part.lower() for part in key_path):
            yield image
        if any("image" in part.lower() for part in key_path):
            yield from compose_repository_images(value)
        image_name = value.get("image")
        image_tag = value.get("imageTag")
        combined_image = (
            f"{image_name}:{image_tag}"
            if isinstance(image_name, str) and image_name and image_tag not in (None, "")
            else None
        )
        if combined_image:
            yield combined_image
        for key, child in value.items():
            key_text = str(key)
            if (
                isinstance(child, str)
                and child
                and "image" in key_text.lower()
                and key_text.lower() != "imagetag"
                and not (key_text == "image" and combined_image)
                and not key_text.lower().endswith(("pullpolicy", "pullsecret", "pullsecrets"))
            ):
                yield child
            yield from image_values(child, (*key_path, key_text))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from image_values(child, (*key_path, str(index)))


def is_pinned_image(reference: str) -> bool:
    if "{{" in reference or reference.endswith(":latest"):
        return False
    if "@sha256:" in reference:
        return True
    return IMAGE_TAG.fullmatch(reference) is not None


def normalize_image_reference(reference: str) -> str:
    """Return one canonical image reference, including Docker Hub's registry."""
    if "{{" in reference:
        return reference
    separator = "@" if "@" in reference else ":"
    if separator == ":" and IMAGE_TAG.fullmatch(reference) is None:
        return reference
    repository, suffix = reference.rsplit(separator, 1)
    first = repository.split("/", 1)[0]
    if "/" not in repository:
        repository = f"docker.io/library/{repository}"
    elif "." not in first and ":" not in first and first != "localhost":
        repository = f"docker.io/{repository}"
    return f"{repository}{separator}{suffix}"


def _manifest_images(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        image = value.get("image")
        if isinstance(image, str) and image:
            yield image
        for child in value.values():
            yield from _manifest_images(child)
    elif isinstance(value, list):
        for child in value:
            yield from _manifest_images(child)


@cache
def rendered_image_inventory() -> dict[str, set[str]]:
    """Render every chart and return images missed by static values discovery."""
    found: dict[str, set[str]] = {}
    for chart_path in sorted((ROOT / "charts").glob("**/Chart.yaml")):
        chart_dir = chart_path.parent
        result = subprocess.run(
            [
                "helm",
                "template",
                chart_dir.name,
                str(chart_dir),
                "--skip-tests",
                "--values",
                str(LINT_VALUES_PATH),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            origin = chart_dir.relative_to(ROOT).as_posix()
            raise ReleaseError(f"failed to render {origin} for image inventory: {result.stderr}")
        origin = (chart_dir / "values.yaml").relative_to(ROOT).as_posix()
        for document in yaml.safe_load_all(result.stdout):
            for reference in _manifest_images(document):
                found.setdefault(normalize_image_reference(reference), set()).add(origin)
    return found


def excluded_chart_prefixes(config: dict[str, Any]) -> list[str]:
    prefixes: list[str] = []
    for package in config["packages"].get("optional", []):
        if package.get("status") == "excluded":
            prefixes.extend(str(prefix) for prefix in package.get("chartPrefixes", []))
    return prefixes


def image_inventory(config: dict[str, Any]) -> list[dict[str, Any]]:
    found: dict[str, set[str]] = {}
    for values_path in sorted((ROOT / "charts").glob("**/values.yaml")):
        values = yaml.safe_load(values_path.read_text())
        origin = values_path.relative_to(ROOT).as_posix()
        for reference in image_values(values):
            found.setdefault(normalize_image_reference(reference), set()).add(origin)
    for reference, origins in rendered_image_inventory().items():
        found.setdefault(reference, set()).update(origins)

    excluded = excluded_chart_prefixes(config)
    images: list[dict[str, Any]] = []
    for reference, origins in sorted(found.items()):
        all_excluded = all(
            any(origin.startswith(f"{prefix}/") for prefix in excluded)
            for origin in origins
        )
        images.append(
            {
                "reference": reference,
                "pinned": is_pinned_image(reference),
                "eligibility": "excluded" if all_excluded else "included",
                "origins": sorted(origins),
            }
        )
    return images


def build_manifest() -> dict[str, Any]:
    config = load_yaml(CONFIG_PATH)
    version = VERSION_PATH.read_text().strip()
    release_date = config["releaseDate"]
    spec = {
        "version": version,
        "releaseDate": None if release_date is None else str(release_date),
        "summary": str(config["summary"]),
        "trust": config["trust"],
        "compatibility": config["compatibility"],
        "packages": config["packages"],
        "prerequisites": config["prerequisites"],
        "exceptions": config.get("exceptions", []),
        "artifacts": {
            "charts": chart_inventory(),
            "helmReleases": helm_release_inventory(),
            "images": image_inventory(config),
            "toolchain": toolchain_inventory(),
        },
    }
    if "provenance" in config:
        spec["provenance"] = config["provenance"]
    return {
        "apiVersion": "platform.neurwerk.com/v1alpha1",
        "kind": "PlatformRelease",
        "metadata": {"name": f"v{version}"},
        "spec": spec,
    }


def rendered_manifest() -> str:
    return yaml.safe_dump(build_manifest(), sort_keys=False, width=100)


def validate_manifest_schema(manifest: dict[str, Any]) -> None:
    """Validate release evidence against the committed JSON Schema."""
    schema = json.loads(SCHEMA_PATH.read_text())
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(manifest, schema)
    except jsonschema.exceptions.SchemaError as exc:
        raise ReleaseError(f"release manifest schema is invalid: {exc.message}") from exc
    except jsonschema.exceptions.ValidationError as exc:
        raise ReleaseError(f"release manifest does not match its schema: {exc.message}") from exc


def changelog_section(changelog: str, version: str) -> str:
    match = re.search(
        rf"^## \[{re.escape(version)}\].*?(?=^## \[|\Z)",
        changelog,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise ReleaseError(f"no changelog section for {version}")
    return match.group(0).strip()


def contains_todo(value: str) -> bool:
    return re.search(r"\bTODO\b", value, flags=re.IGNORECASE) is not None


def migration_section(migration: str, heading: str) -> str:
    matches = re.findall(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)",
        migration,
        flags=re.MULTILINE | re.DOTALL,
    )
    if len(matches) != 1:
        raise ReleaseError(
            f"release migration must contain exactly one ## {heading} section"
        )
    return matches[0]


def migration_declaration(
    migration: str,
    section: str,
    heading: str,
    prefix: str,
    name: str,
) -> str:
    declarations = [line for line in migration.splitlines() if line.startswith(prefix)]
    if len(declarations) != 1:
        raise ReleaseError(
            f"release migration must contain exactly one {name} declaration"
        )
    if declarations[0] not in section.splitlines():
        raise ReleaseError(f"release migration {name} declaration must appear in ## {heading}")
    match = re.fullmatch(
        rf"{re.escape(prefix)}([A-Z][a-z]+(?: [a-z]+)*)\.", declarations[0]
    )
    if match is None:
        raise ReleaseError(f"release migration {name} declaration has invalid format")
    return match.group(1)


def migration_source_versions(migration: str, support: str) -> list[str]:
    prefix = "- Supported source versions: "
    lines = migration.splitlines()
    declaration_indexes = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    if len(declaration_indexes) != 1:
        raise ReleaseError(
            "release migration must contain exactly one supported source versions declaration"
        )
    index = declaration_indexes[0]
    if lines[index] not in support.splitlines():
        raise ReleaseError(
            "release migration supported source versions declaration must appear in ## Support"
        )

    fragments = [lines[index].removeprefix(prefix)]
    if not fragments[0]:
        raise ReleaseError(
            "release migration supported source versions declaration has invalid format"
        )
    while not fragments[-1].endswith("."):
        index += 1
        if index >= len(lines) or re.fullmatch(r"  \S.*", lines[index]) is None:
            raise ReleaseError(
                "release migration supported source versions declaration has invalid continuation"
            )
        fragments.append(lines[index][2:])
    if index + 1 < len(lines) and re.fullmatch(r"  \S.*", lines[index + 1]):
        raise ReleaseError(
            "release migration supported source versions declaration has unexpected continuation"
        )

    value = " ".join(fragments)
    if value == "None.":
        return []
    if not value.endswith("."):
        raise ReleaseError(
            "release migration supported source versions declaration has invalid format"
        )
    encoded_tags = value[:-1]
    tags = re.findall(r"`([^`]+)`", encoded_tags)
    canonical = ", ".join(f"`{tag}`" for tag in tags)
    if not tags or canonical != encoded_tags or any(
        RELEASE_TAG.fullmatch(tag) is None for tag in tags
    ):
        raise ReleaseError(
            "release migration supported source versions must be None or comma-separated strict tags"
        )
    if len(tags) != len(set(tags)):
        raise ReleaseError("release migration supported source versions contain duplicate tags")
    return tags


def parse_migration_compatibility(migration: str) -> dict[str, Any]:
    support = migration_section(migration, "Support")
    recovery_section = migration_section(migration, "Recovery")

    fresh_install = migration_declaration(
        migration,
        support,
        "Support",
        "- Fresh installation: ",
        "fresh installation",
    ).lower()
    if fresh_install not in ("supported", "unsupported"):
        raise ReleaseError(
            f"release migration has unknown fresh installation value: {fresh_install}"
        )
    upgrades_from = migration_source_versions(migration, support)
    downgrade = migration_declaration(
        migration, support, "Support", "- Downgrade: ", "downgrade"
    ).lower()
    if downgrade not in ("supported", "unsupported"):
        raise ReleaseError(f"release migration has unknown downgrade value: {downgrade}")

    recovery_label = migration_declaration(
        migration,
        recovery_section,
        "Recovery",
        "Recovery classification: ",
        "recovery classification",
    )
    recovery = recovery_label.lower().replace(" ", "-")
    if recovery not in RECOVERY_ACTIONS:
        raise ReleaseError(f"release migration has unknown recovery classification: {recovery}")
    return {
        "freshInstall": fresh_install,
        "upgradesFrom": upgrades_from,
        "downgrade": downgrade,
        "recovery": recovery,
    }


def validate_migration_compatibility(
    migration: str, compatibility: Any, source: str = "release compatibility"
) -> None:
    if not isinstance(compatibility, dict):
        raise ReleaseError(f"{source} must be an object")
    declared = parse_migration_compatibility(migration)
    for field in ("freshInstall", "downgrade", "recovery"):
        if declared[field] != compatibility.get(field):
            raise ReleaseError(
                f"release migration {field} does not match {source}.{field}"
            )
    expected_upgrades = compatibility.get("upgradesFrom")
    if declared["upgradesFrom"] != expected_upgrades:
        if (
            isinstance(expected_upgrades, list)
            and all(isinstance(tag, str) for tag in expected_upgrades)
            and len(declared["upgradesFrom"]) == len(expected_upgrades)
            and set(declared["upgradesFrom"]) == set(expected_upgrades)
        ):
            mismatch = "order"
        else:
            mismatch = "set"
        raise ReleaseError(
            f"release migration upgradesFrom {mismatch} does not match {source}.upgradesFrom"
        )


def is_release_evidence_path(path: str, version: str) -> bool:
    return path in {
        "VERSION",
        "CHANGELOG.md",
        "release/config.yaml",
        "release/manifest.yaml",
        f"release/migrations/v{version}.md",
    }


def validate_release_prose(config: dict[str, Any], version: str, errors: list[str]) -> None:
    migration = ROOT / f"release/migrations/v{version}.md"
    if not migration.is_file():
        errors.append(f"missing {migration.relative_to(ROOT)}")
    changelog = CHANGELOG_PATH.read_text()
    if f"## [{version}] - {config.get('releaseDate')}" not in changelog:
        errors.append("CHANGELOG.md has no dated entry for this release")
    else:
        section = changelog_section(changelog, version)
        if contains_todo(section):
            errors.append("release changelog section contains TODO markers")
    if migration.is_file():
        migration_text = migration.read_text()
        if contains_todo(migration_text):
            errors.append("release migration document contains TODO markers")
        try:
            validate_migration_compatibility(
                migration_text, config.get("compatibility"), "release config compatibility"
            )
        except ReleaseError as exc:
            errors.append(str(exc))
    if contains_todo(str(config.get("summary", ""))):
        errors.append("release summary contains TODO markers")


def validate_provenance(config: dict[str, Any], version: str, errors: list[str]) -> None:
    provenance = config.get("provenance")
    if version == UNPUBLISHED_BASELINE_VERSION:
        if provenance is not None:
            errors.append(
                f"unpublished baseline {version} must not declare release provenance"
            )
        return
    if provenance is None:
        errors.append("release provenance is missing")
        return
    try:
        mode = provenance_mode(provenance)
    except ReleaseError as exc:
        errors.append(str(exc))
        return

    if version == BOOTSTRAP_RELEASE_VERSION and mode != "bootstrap":
        errors.append(
            f"bootstrap release {BOOTSTRAP_RELEASE_TAG} requires bootstrap provenance"
        )
        return
    if version != BOOTSTRAP_RELEASE_VERSION and mode == "bootstrap":
        errors.append(
            f"bootstrap provenance is allowed only for {BOOTSTRAP_RELEASE_TAG}"
        )
        return

    included_through = provenance.get("includedThrough")
    commits = provenance.get("commits")
    if not isinstance(included_through, str) or GIT_COMMIT.fullmatch(included_through) is None:
        errors.append("release provenance includedThrough is not a full commit")
        return
    if not isinstance(commits, list) or not commits or not all(
        isinstance(commit, str) and GIT_COMMIT.fullmatch(commit) for commit in commits
    ):
        errors.append("release provenance commits must be a non-empty list of full commits")
        return
    if commits[-1] != included_through:
        errors.append("release provenance includedThrough must equal the final commit")

    if mode == "bootstrap":
        expected_url = f"{REPOSITORY_URL}/commits/{included_through}"
        if provenance.get("historyUrl") != expected_url:
            errors.append("release provenance historyUrl is not canonical")
        try:
            expected = bootstrap_provenance_from_git(included_through)
        except ReleaseError as exc:
            errors.append(str(exc))
            return
        if commits != expected["commits"]:
            errors.append(
                "release provenance commit list does not match complete Git history"
            )
    else:
        previous_tag = provenance.get("previousTag")
        if not isinstance(previous_tag, str) or RELEASE_TAG.fullmatch(previous_tag) is None:
            errors.append("release provenance previousTag is not a strict release tag")
            return
        expected_url = f"{REPOSITORY_URL}/compare/{previous_tag}...{included_through}"
        if provenance.get("compareUrl") != expected_url:
            errors.append("release provenance compareUrl is not canonical")
        try:
            expected = provenance_from_git(previous_tag, included_through)
        except ReleaseError as exc:
            errors.append(str(exc))
            return
        if commits != expected["commits"]:
            errors.append("release provenance commit list does not match Git history")
        try:
            validate_previous_tag_at_included_through(previous_tag, included_through)
        except ReleaseError as exc:
            errors.append(str(exc))

    omitted_paths = git("diff", "--name-only", f"{included_through}..HEAD").splitlines()
    unexpected_paths = [
        path for path in omitted_paths if not is_release_evidence_path(path, version)
    ]
    if unexpected_paths:
        errors.append(
            "non-evidence changes exist after provenance includedThrough: "
            + ", ".join(unexpected_paths)
        )
    if not git_is_ancestor(included_through, "HEAD"):
        errors.append("release provenance includedThrough is not an ancestor of HEAD")


def validate(tag: str | None = None) -> None:
    config = load_yaml(CONFIG_PATH)
    version = VERSION_PATH.read_text().strip()
    errors: list[str] = []

    if not SEMVER.fullmatch(version):
        errors.append(f"VERSION is not SemVer: {version!r}")
    if str(config.get("version")) != version:
        errors.append("release/config.yaml version does not match VERSION")
    if version == UNPUBLISHED_BASELINE_VERSION:
        if config.get("releaseDate") is not None:
            errors.append("unpublished baseline releaseDate must be null")
    else:
        try:
            validate_release_date(str(config.get("releaseDate", "")))
        except ReleaseError as exc:
            errors.append(str(exc))
    if tag and tag != f"v{version}":
        errors.append(f"tag {tag!r} does not match v{version}")
    if tag and RELEASE_TAG.fullmatch(tag) is None:
        errors.append(f"tag is not strict SemVer: {tag!r}")
    if (
        tag
        and SEMVER.fullmatch(version)
        and semver_tuple(version) < semver_tuple(BOOTSTRAP_RELEASE_VERSION)
    ):
        errors.append(
            f"unpublished pre-v{BOOTSTRAP_RELEASE_VERSION} history cannot be released"
        )

    validate_provenance(config, version, errors)
    previous_tag = config.get("provenance", {}).get("previousTag")
    if isinstance(previous_tag, str) and RELEASE_TAG.fullmatch(previous_tag):
        previous_version = previous_tag.removeprefix("v")
        if SEMVER.fullmatch(version) and semver_tuple(version) <= semver_tuple(previous_version):
            errors.append("release version is not newer than provenance previousTag")

    compatibility = config.get("compatibility")
    if version == BOOTSTRAP_RELEASE_VERSION and (
        not isinstance(compatibility, dict)
        or compatibility.get("freshInstall") != "supported"
        or compatibility.get("upgradesFrom") != []
        or compatibility.get("downgrade") != "unsupported"
    ):
        errors.append(
            f"bootstrap release {BOOTSTRAP_RELEASE_TAG} must be fresh-install-only"
        )

    trust = config.get("trust", {})
    key_algorithm, key_fingerprint, key_comment = public_key_fingerprint()
    if trust.get("signerIdentity") != "platform-release":
        errors.append("release signer identity must be platform-release")
    if trust.get("algorithm") != "ssh-ed25519" or key_algorithm != "ssh-ed25519":
        errors.append("release signer algorithm must be ssh-ed25519")
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", str(trust.get("fingerprint", ""))):
        errors.append("release signer fingerprint is not a SHA-256 SSH fingerprint")
    if trust.get("fingerprint") != key_fingerprint:
        errors.append("release public key does not match the approved fingerprint")
    if key_comment != "platform-release@neurwerk":
        errors.append("release public key has the wrong identity comment")

    if version != UNPUBLISHED_BASELINE_VERSION:
        validate_release_prose(config, version, errors)

    for package in config.get("packages", {}).get("default", []):
        if not (ROOT / str(package)).is_dir():
            errors.append(f"default package does not exist: {package}")
    for package in config.get("packages", {}).get("optional", []):
        if not (ROOT / str(package.get("path"))).is_dir():
            errors.append(f"optional package does not exist: {package.get('path')}")

    exceptions = {
        str(item.get("artifact")): item for item in config.get("exceptions", [])
    }
    for image in image_inventory(config):
        if image["eligibility"] == "included" and not image["pinned"]:
            exception = exceptions.get(image["reference"])
            if exception is None:
                errors.append(f"included image is not pinned: {image['reference']}")
            elif not all(exception.get(field) for field in ("owner", "expires", "reason")):
                errors.append(f"image exception is incomplete: {image['reference']}")

    manifest = build_manifest()
    validate_manifest_schema(manifest)
    expected = yaml.safe_dump(manifest, sort_keys=False, width=100)
    actual = MANIFEST_PATH.read_text() if MANIFEST_PATH.exists() else ""
    if actual != expected:
        errors.append("release/manifest.yaml is stale; run make release-manifest")

    if errors:
        raise ReleaseError("\n".join(f"- {error}" for error in errors))


def inspect_release_data(
    release_root: Path,
    tag: str,
    minimum_version: str = MINIMUM_PROVENANCE_RELEASE,
) -> dict[str, Any]:
    release_root = release_root.resolve()
    if RELEASE_TAG.fullmatch(tag) is None:
        raise ReleaseError(f"invalid release tag: {tag!r}")

    version_path = release_root / "VERSION"
    config_path = release_root / "release/config.yaml"
    manifest_path = release_root / "release/manifest.yaml"
    for path in (version_path, config_path, manifest_path):
        if not path.is_file():
            raise ReleaseError(f"release {tag} is missing required contract file {path.name}")

    version = version_path.read_text().strip()
    if SEMVER.fullmatch(version) is None or tag != f"v{version}":
        raise ReleaseError(f"release tag {tag} does not match VERSION {version!r}")
    if semver_tuple(version) < semver_tuple(minimum_version):
        raise ReleaseError(
            f"release {tag} predates the required provenance contract; "
            f"client adoption requires v{minimum_version} or newer"
        )

    config = load_yaml(config_path)
    manifest = load_yaml(manifest_path)
    validate_manifest_schema(manifest)
    if str(config.get("version")) != version:
        raise ReleaseError("release config version does not match VERSION")
    if manifest.get("metadata", {}).get("name") != tag:
        raise ReleaseError("release manifest metadata.name does not match the tag")
    spec = manifest.get("spec", {})
    if str(spec.get("version")) != version:
        raise ReleaseError("release manifest version does not match VERSION")
    if str(config.get("releaseDate")) != str(spec.get("releaseDate")):
        raise ReleaseError("release config and manifest dates do not match")
    validate_release_date(str(config.get("releaseDate")))
    config_compatibility = config.get("compatibility")
    manifest_compatibility = spec.get("compatibility")
    if config_compatibility != manifest_compatibility:
        raise ReleaseError("release config and manifest compatibility do not match")

    provenance = config.get("provenance")
    if spec.get("provenance") != provenance:
        raise ReleaseError("release config and manifest provenance do not match")
    mode = provenance_mode(provenance)
    included_through = provenance.get("includedThrough")
    commits = provenance.get("commits")
    if not isinstance(included_through, str) or GIT_COMMIT.fullmatch(included_through) is None:
        raise ReleaseError("release provenance has no full includedThrough commit")
    if not isinstance(commits, list) or not commits or not all(
        isinstance(commit, str) and GIT_COMMIT.fullmatch(commit) for commit in commits
    ):
        raise ReleaseError("release provenance commits are missing or invalid")
    if commits[-1] != included_through:
        raise ReleaseError("release provenance includedThrough is not the final commit")

    tag_commit = git("rev-parse", "--verify", f"{tag}^{{commit}}", repository=release_root)
    if not git_is_ancestor(included_through, tag_commit, repository=release_root):
        raise ReleaseError("includedThrough is not an ancestor of the release tag")

    if mode == "bootstrap":
        if version != BOOTSTRAP_RELEASE_VERSION or tag != BOOTSTRAP_RELEASE_TAG:
            raise ReleaseError(
                f"bootstrap provenance is allowed only for {BOOTSTRAP_RELEASE_TAG}"
            )
        if (
            not isinstance(config_compatibility, dict)
            or config_compatibility.get("freshInstall") != "supported"
            or config_compatibility.get("upgradesFrom") != []
            or config_compatibility.get("downgrade") != "unsupported"
        ):
            raise ReleaseError(
                f"bootstrap release {BOOTSTRAP_RELEASE_TAG} must be fresh-install-only"
            )
        expected = bootstrap_provenance_from_git(included_through, release_root)
        if commits != expected["commits"]:
            raise ReleaseError(
                "release provenance commit list does not match complete Git history"
            )
        if provenance.get("historyUrl") != expected["historyUrl"]:
            raise ReleaseError("release provenance historyUrl is not canonical")
        older_tags = [
            candidate
            for candidate in repository_tags(release_root)
            if RELEASE_TAG.fullmatch(candidate)
            and semver_tuple(candidate.removeprefix("v"))
            < semver_tuple(BOOTSTRAP_RELEASE_VERSION)
        ]
        if older_tags:
            raise ReleaseError(
                "bootstrap release cannot follow existing platform release tags: "
                + ", ".join(older_tags)
            )
    else:
        if version == BOOTSTRAP_RELEASE_VERSION:
            raise ReleaseError(
                f"bootstrap release {BOOTSTRAP_RELEASE_TAG} requires bootstrap provenance"
            )
        previous_tag = provenance.get("previousTag")
        if not isinstance(previous_tag, str) or RELEASE_TAG.fullmatch(previous_tag) is None:
            raise ReleaseError("release provenance has no strict previousTag")
        validate_previous_tag_at_included_through(
            previous_tag, included_through, repository=release_root
        )
        previous_version = git(
            "show", f"{previous_tag}:VERSION", repository=release_root
        ).strip()
        if previous_tag != f"v{previous_version}":
            raise ReleaseError("previousTag does not match VERSION in the previous release")
        previous_commit = git(
            "rev-parse", "--verify", f"{previous_tag}^{{commit}}", repository=release_root
        )
        if not git_is_ancestor(previous_commit, included_through, repository=release_root):
            raise ReleaseError("previousTag is not an ancestor of includedThrough")
        expected_commits = git(
            "rev-list",
            "--reverse",
            f"{previous_tag}..{included_through}",
            repository=release_root,
        ).splitlines()
        if commits != expected_commits or not expected_commits:
            raise ReleaseError("release provenance commit list does not match Git history")
        expected_url = f"{REPOSITORY_URL}/compare/{previous_tag}...{included_through}"
        if provenance.get("compareUrl") != expected_url:
            raise ReleaseError("release provenance compareUrl is not canonical")

    changed_after = git(
        "diff", "--name-only", f"{included_through}..{tag_commit}", repository=release_root
    ).splitlines()
    unexpected = [path for path in changed_after if not is_release_evidence_path(path, version)]
    if unexpected:
        raise ReleaseError(
            "non-evidence changes exist after provenance includedThrough: "
            + ", ".join(unexpected)
        )

    changelog = (release_root / "CHANGELOG.md").read_text()
    migration = release_root / f"release/migrations/v{version}.md"
    if not migration.is_file() or contains_todo(migration.read_text()):
        raise ReleaseError("release migration evidence is missing or incomplete")
    validate_migration_compatibility(
        migration.read_text(), config_compatibility, "release config compatibility"
    )
    validate_migration_compatibility(
        migration.read_text(), manifest_compatibility, "release manifest compatibility"
    )
    if contains_todo(changelog_section(changelog, version)):
        raise ReleaseError("release changelog contains TODO markers")
    return provenance


def render_release_notes(release_root: Path, generated_notes: Path | None = None) -> str:
    version = (release_root / "VERSION").read_text().strip()
    migration = release_root / f"release/migrations/v{version}.md"
    changelog = (release_root / "CHANGELOG.md").read_text()
    content = (
        f"# Neurwerk Platform v{version}\n\n"
        f"{changelog_section(changelog, version)}\n\n"
        f"{migration.read_text().strip()}\n"
    )
    if generated_notes:
        generated = generated_notes.read_text().strip()
        if not generated:
            raise ReleaseError("generated GitHub release notes are empty")
        content += f"\n## Pull Requests And Contributors\n\n{generated}\n"
    return content


def parse_upgrades_from(value: str) -> list[str]:
    if not value.strip():
        return []
    tags = [item.strip() for item in value.split(",")]
    if any(RELEASE_TAG.fullmatch(tag) is None for tag in tags):
        raise ReleaseError("upgrades-from must be a comma-separated list of strict release tags")
    if len(tags) != len(set(tags)):
        raise ReleaseError("upgrades-from contains duplicate tags")
    return tags


def prepare_release(args: argparse.Namespace) -> None:
    current_version = VERSION_PATH.read_text().strip()
    if SEMVER.fullmatch(current_version) is None:
        raise ReleaseError(f"current VERSION is not strict SemVer: {current_version!r}")
    bootstrap = bool(getattr(args, "bootstrap", False))
    target = semver_tuple(args.version)
    validate_release_date(args.release_date)
    upgrades_from = parse_upgrades_from(args.upgrades_from)

    if bootstrap:
        if current_version != UNPUBLISHED_BASELINE_VERSION:
            raise ReleaseError(
                "bootstrap requires the exact unpublished "
                f"{UNPUBLISHED_BASELINE_VERSION} baseline"
            )
        if args.version != BOOTSTRAP_RELEASE_VERSION:
            raise ReleaseError(f"bootstrap target must be {BOOTSTRAP_RELEASE_TAG}")
        tags = repository_tags()
        if tags:
            raise ReleaseError(
                "bootstrap requires a repository with zero tags; found: "
                + ", ".join(tags)
            )
        if args.fresh_install != "supported" or upgrades_from:
            raise ReleaseError(
                "bootstrap compatibility must support fresh installation and no upgrades"
            )
        provenance = bootstrap_provenance_from_git()
    else:
        previous_tag = getattr(args, "previous_tag", None)
        expected_previous_tag = f"v{current_version}"
        if previous_tag != expected_previous_tag:
            raise ReleaseError(
                f"previous-tag must match current VERSION: expected {expected_previous_tag}"
            )
        latest_tag = latest_release_tag()
        if previous_tag != latest_tag:
            raise ReleaseError(
                f"previous-tag must be the latest release reachable from HEAD: {latest_tag}"
            )
        verify_release_tag_signature(previous_tag, previous_tag)

        previous_match = RELEASE_TAG.fullmatch(previous_tag)
        if previous_match is None:
            raise ReleaseError(f"invalid previous release tag: {previous_tag!r}")
        previous = tuple(int(part) for part in previous_match.groups())
        if target <= previous:
            raise ReleaseError("target version must be newer than the previous release tag")
        for source_tag in upgrades_from:
            git("rev-parse", "--verify", f"{source_tag}^{{commit}}")
        provenance = provenance_from_git(previous_tag)

    config = load_yaml(CONFIG_PATH)
    config["version"] = args.version
    config["releaseDate"] = args.release_date
    config["summary"] = args.summary.strip()
    if not config["summary"]:
        raise ReleaseError("summary must not be empty")
    config["provenance"] = provenance
    config["compatibility"] = {
        "freshInstall": args.fresh_install,
        "upgradesFrom": upgrades_from,
        "downgrade": "unsupported",
        "recovery": args.recovery,
    }

    VERSION_PATH.write_text(f"{args.version}\n")
    CONFIG_PATH.write_text(yaml.safe_dump(config, sort_keys=False, width=100))

    changelog = CHANGELOG_PATH.read_text()
    if re.search(rf"^## \[{re.escape(args.version)}\]", changelog, flags=re.MULTILINE) is None:
        marker = "## [Unreleased]\n"
        if marker not in changelog:
            raise ReleaseError("CHANGELOG.md has no Unreleased section")
        entry = (
            f"\n## [{args.version}] - {args.release_date}\n\n"
            "### TODO: Curate Changes\n\n"
            "- TODO: Replace this scaffold with reviewed release notes.\n\n"
            "### Compatibility\n\n"
            "- TODO: Describe exact compatibility and recovery behavior.\n"
        )
        CHANGELOG_PATH.write_text(changelog.replace(marker, f"{marker}{entry}", 1))

    migration = ROOT / f"release/migrations/v{args.version}.md"
    if not migration.exists():
        migration.parent.mkdir(parents=True, exist_ok=True)
        supported = ", ".join(f"`{tag}`" for tag in upgrades_from) or "None"
        migration.write_text(
            f"# Platform v{args.version}\n\n"
            "> TODO: Replace every TODO with reviewed release-specific evidence.\n\n"
            "## Support\n\n"
            f"- Fresh installation: {args.fresh_install.title()}.\n"
            f"- Supported source versions: {supported}.\n"
            "- Downgrade: Unsupported.\n\n"
            "## Prerequisites\n\nTODO.\n\n"
            "## Client Actions\n\nTODO.\n\n"
            "## Stateful And API Effects\n\nTODO.\n\n"
            "## Pre-Deployment Checks\n\nTODO.\n\n"
            "## Post-Deployment Checks\n\nTODO.\n\n"
            "## Recovery\n\n"
            f"Recovery classification: {args.recovery.replace('-', ' ').capitalize()}.\n\n"
            "TODO.\n\n"
            "## Exclusions\n\nTODO.\n"
        )

    manifest = build_manifest()
    validate_manifest_schema(manifest)
    MANIFEST_PATH.write_text(yaml.safe_dump(manifest, sort_keys=False, width=100))


def update_client_source(path: Path, tag: str) -> None:
    if RELEASE_TAG.fullmatch(tag) is None:
        raise ReleaseError(f"invalid release tag: {tag!r}")
    data = load_yaml(path)
    metadata = data.get("metadata", {})
    spec = data.get("spec", {})
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise ReleaseError("client source must use canonical mappings")
    annotations = metadata.get("annotations", {})
    reference = spec.get("ref", {})
    verification = spec.get("verify", {})
    if not all(
        isinstance(value, dict)
        for value in (annotations, reference, verification)
    ):
        raise ReleaseError("client source must use canonical mappings")
    secret_reference = verification.get("secretRef", {})
    mappings = (
        metadata,
        spec,
        annotations,
        reference,
        verification,
        secret_reference,
    )
    if not all(isinstance(value, dict) for value in mappings):
        raise ReleaseError("client source must use canonical mappings")
    if set(data) != {"apiVersion", "kind", "metadata", "spec"}:
        raise ReleaseError("client source must use the canonical source shape")
    if data.get("apiVersion") != "source.toolkit.fluxcd.io/v1":
        raise ReleaseError("client source must use the canonical source API")
    if data.get("kind") != "GitRepository":
        raise ReleaseError("client source must be GitRepository/k8s-stack")
    if set(metadata) != {"annotations", "name", "namespace"}:
        raise ReleaseError("client source must use canonical metadata")
    if (
        metadata.get("name") != "k8s-stack"
        or metadata.get("namespace") != "flux-system"
    ):
        raise ReleaseError(
            "client source must be GitRepository/k8s-stack in flux-system"
        )
    if set(annotations) != {
        "platform.neurwerk.com/adoption-mode",
        "platform.neurwerk.com/adoption-target",
    }:
        raise ReleaseError("client source must use canonical adoption annotations")
    if set(spec) != {"interval", "url", "ref", "verify"}:
        raise ReleaseError("client source must use the canonical source spec")
    if spec.get("interval") != "30s":
        raise ReleaseError("client source must retain the canonical interval")
    if spec.get("url") != f"{REPOSITORY_URL}.git":
        raise ReleaseError(
            "client source does not use the canonical platform repository"
        )
    if set(reference) != {"tag"}:
        raise ReleaseError("client source must select exactly one platform tag")
    if set(verification) != {"mode", "secretRef"} or set(secret_reference) != {
        "name"
    }:
        raise ReleaseError("client source must use canonical tag verification")
    if verification.get("mode") != "Tag":
        raise ReleaseError("client source must retain tag verification")
    if secret_reference.get("name") != "k8s-stack-release-trust":
        raise ReleaseError("client source must retain the release trust reference")
    current_tag = reference.get("tag")
    if RELEASE_TAG.fullmatch(str(current_tag)) is None:
        raise ReleaseError("client source must contain one strict current platform tag")
    if annotations.get("platform.neurwerk.com/adoption-target") != current_tag:
        raise ReleaseError("client source adoption target must match its current tag")
    if annotations.get("platform.neurwerk.com/adoption-mode") not in {
        "fresh-install",
        "upgrade",
    }:
        raise ReleaseError("client source must contain a reviewed adoption mode")

    content = path.read_text()
    replacements = (
        (
            re.compile(
                r"^(?P<prefix>[ \t]*tag:[ \t]*)(?P<quote>['\"]?)"
                r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
                r"(?P=quote)(?P<suffix>[ \t]*(?:#[^\r\n]*)?)$",
                re.MULTILINE,
            ),
            rf"\g<prefix>\g<quote>{tag}\g<quote>\g<suffix>",
            "platform tag",
        ),
        (
            re.compile(
                r"^(?P<prefix>[ \t]*platform\.neurwerk\.com/adoption-target:[ \t]*)"
                r"(?P<quote>['\"]?)"
                r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
                r"(?P=quote)(?P<suffix>[ \t]*(?:#[^\r\n]*)?)$",
                re.MULTILINE,
            ),
            rf"\g<prefix>\g<quote>{tag}\g<quote>\g<suffix>",
            "adoption target",
        ),
        (
            re.compile(
                r"^(?P<prefix>[ \t]*platform\.neurwerk\.com/adoption-mode:[ \t]*)"
                r"(?P<quote>['\"]?)(?:fresh-install|upgrade)(?P=quote)"
                r"(?P<suffix>[ \t]*(?:#[^\r\n]*)?)$",
                re.MULTILINE,
            ),
            r"\g<prefix>\g<quote>review-required\g<quote>\g<suffix>",
            "adoption mode",
        ),
    )
    updated = content
    for pattern, replacement, field in replacements:
        updated, count = pattern.subn(replacement, updated)
        if count != 1:
            raise ReleaseError(f"client source must contain exactly one {field}")
    path.write_text(updated)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("generate")
    check = subparsers.add_parser("check")
    check.add_argument("--tag")
    notes = subparsers.add_parser("notes")
    notes.add_argument("--output", type=Path, required=True)
    notes.add_argument("--generated-notes", type=Path)
    notes.add_argument("--release-root", type=Path, default=ROOT)
    previous_tag = subparsers.add_parser("previous-tag")
    previous_tag.add_argument("--release-root", type=Path, default=ROOT)
    tag_for_commit = subparsers.add_parser("tag-for-commit")
    tag_for_commit.add_argument("--commit", required=True)
    tag_for_commit.add_argument("--repository", type=Path, default=ROOT)
    inspect = subparsers.add_parser("inspect-release")
    inspect.add_argument("--release-root", type=Path, required=True)
    inspect.add_argument("--tag", required=True)
    inspect.add_argument("--minimum-version", default=MINIMUM_PROVENANCE_RELEASE)
    inspect.add_argument("--field", choices=("previous-tag", "provenance-mode"))
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--version", required=True)
    preparation_mode = prepare.add_mutually_exclusive_group(required=True)
    preparation_mode.add_argument("--bootstrap", action="store_true")
    preparation_mode.add_argument("--previous-tag")
    prepare.add_argument("--release-date", required=True)
    prepare.add_argument("--summary", required=True)
    prepare.add_argument("--fresh-install", choices=("supported", "unsupported"), required=True)
    prepare.add_argument("--upgrades-from", default="")
    prepare.add_argument("--recovery", choices=RECOVERY_ACTIONS, required=True)
    client = subparsers.add_parser("update-client-source")
    client.add_argument("--path", type=Path, required=True)
    client.add_argument("--tag", required=True)
    args = parser.parse_args()

    try:
        if args.command == "generate":
            MANIFEST_PATH.write_text(rendered_manifest())
        elif args.command == "check":
            validate(args.tag)
        elif args.command == "notes":
            args.output.write_text(
                render_release_notes(args.release_root.resolve(), args.generated_notes)
            )
        elif args.command == "previous-tag":
            config = load_yaml(args.release_root.resolve() / "release/config.yaml")
            provenance = config.get("provenance")
            if provenance_mode(provenance) != "predecessor":
                raise ReleaseError("bootstrap release has no previous tag")
            print(provenance["previousTag"])
        elif args.command == "tag-for-commit":
            print(release_tag_for_commit(args.commit, args.repository.resolve()))
        elif args.command == "inspect-release":
            provenance = inspect_release_data(
                args.release_root, args.tag, args.minimum_version
            )
            if args.field == "previous-tag":
                if provenance_mode(provenance) != "predecessor":
                    raise ReleaseError("bootstrap release has no previous tag")
                print(provenance["previousTag"])
            elif args.field == "provenance-mode":
                print(provenance_mode(provenance))
        elif args.command == "prepare":
            prepare_release(args)
        else:
            update_client_source(args.path.resolve(), args.tag)
    except (KeyError, OSError, ReleaseError, yaml.YAMLError) as exc:
        print(f"platform release error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
