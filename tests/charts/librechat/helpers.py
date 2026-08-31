"""Shared Helm rendering helpers for LibreChat chart contract tests."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"
RESOURCE_VALUES = ROOT / "releases/shared/resources.yaml"


def render_chart(
    chart: str,
    *,
    release_name: str | None = None,
    namespace: str = "frontend-librechat",
    values: tuple[Path, ...] = (),
    platform_values: bool = True,
    extra_args: tuple[str, ...] = (),
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Render one first-party LibreChat chart with deterministic values."""
    command = [
        "helm",
        "template",
        release_name or Path(chart).name,
        str(ROOT / "charts/librechat" / chart),
        "--namespace",
        namespace,
    ]
    if platform_values:
        for path in (LINT_VALUES, RESOURCE_VALUES):
            command.extend(("--values", str(path)))
    for path in values:
        command.extend(("--values", str(path)))
    command.extend(extra_args)

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(command)} failed:\n{result.stderr}{result.stdout}"
        )
    return result


def documents(manifest: str) -> list[str]:
    """Split a rendered Helm manifest into non-empty resource documents."""
    return [
        document
        for document in re.split(r"(?m)^---\s*$", manifest)
        if document.strip()
    ]


def resource(manifest: str, kind: str, name: str) -> str:
    """Return exactly one resource by kind and metadata name."""
    matches = [
        document
        for document in documents(manifest)
        if re.search(rf"(?m)^kind:\s*{re.escape(kind)}\s*$", document)
        and re.search(rf"(?m)^  name:\s*{re.escape(name)}\s*$", document)
    ]
    if len(matches) != 1:
        raise AssertionError(f"Expected one {kind} {name}, found {len(matches)}")
    return matches[0]


def resources_of_kind(manifest: str, kind: str) -> list[str]:
    """Return all rendered resources of one kind."""
    return [
        document
        for document in documents(manifest)
        if re.search(rf"(?m)^kind:\s*{re.escape(kind)}\s*$", document)
    ]


def non_secret_documents(manifest: str) -> str:
    """Join every rendered document except Kubernetes Secrets."""
    return "\n---\n".join(
        document
        for document in documents(manifest)
        if not re.search(r"(?m)^kind:\s*Secret\s*$", document)
    )


def secret_ref_names(document: str) -> set[str]:
    """Return names referenced through secretKeyRef blocks."""
    return set(
        re.findall(
            r"(?m)^\s+secretKeyRef:\s*\n\s+name:\s*([^\s]+)\s*$",
            document,
        )
    )
