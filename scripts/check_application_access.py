#!/usr/bin/env python3
"""Offline consistency checks for an explicit application access plan, not enforcement."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


# Human browser/native-client dependencies only, not backend service traffic.
BROWSER_DEPENDENCIES = {
    "keycloak": (),
    "librechat": ("keycloak",),
    "librechat-admin": ("librechat", "keycloak"),
    "librechat-files": (),
    "studio": ("keycloak",),
    "dify": (),
    "langfuse": (),
    "agentgateway": (),
    "forgejo": ("keycloak",),
}
OPTIONAL_BROWSER_DEPENDENCIES = {
    "librechat": {"files": "librechat-files"},
    "dify": {"consoleSSO": "keycloak"},
}
LEVELS = ("internal", "public", "restricted")


class PlanError(ValueError):
    """An input error whose message contains no supplied values."""


class PlanLoader(yaml.SafeLoader):
    """Reject tags, aliases, duplicate keys and non-string keys before validation."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise PlanError("YAML aliases are not supported")
        if self.peek_event().tag is not None:
            raise PlanError("explicit YAML tags are not supported")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> dict:
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if type(key) is not str:
                raise PlanError("YAML mapping keys must be strings")
            if key in result:
                raise PlanError("duplicate YAML mapping key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def load_plan(path: Path) -> Any:
    """Read only the supplied local file; never expose parser snippets or values."""
    try:
        with path.open(encoding="utf-8") as stream:
            return yaml.load(stream, Loader=PlanLoader)
    except (OSError, UnicodeError):
        raise PlanError("cannot read plan as UTF-8") from None
    except (yaml.YAMLError, RecursionError, ValueError, OverflowError) as error:
        if isinstance(error, PlanError):
            raise
        raise PlanError("invalid or unsupported YAML; input contents withheld") from None


def _mapping(
    value: Any, path: str, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict:
    if type(value) is not dict:
        raise PlanError(f"{path}: expected a mapping")
    if any(type(key) is not str or key not in required + optional for key in value):
        raise PlanError(f"{path}: unknown field (field name withheld)")
    for key in required:
        if key not in value:
            raise PlanError(f"{path}.{key}: required")
    return value


def _choice(value: Any, path: str, choices: tuple[str, ...]) -> str:
    if type(value) is not str or value not in choices:
        raise PlanError(f"{path}: expected one of {', '.join(choices)}")
    return value


def validate_plan(plan: Any) -> tuple[list[str], list[str]]:
    """Return dependency errors and warnings; raise PlanError for malformed shape.

    Inputs are never mutated. Endpoint presence is explicit plan selection only.
    """
    plan = _mapping(
        plan, "plan", ("access", "certificates", "canonicalEndpointRouting", "endpoints")
    )
    access = _mapping(plan["access"], "access", ("boundary", "default"))
    boundary = _choice(access["boundary"], "access.boundary", ("internet", "client-network"))
    default = _choice(access["default"], "access.default", LEVELS)
    certificates = _mapping(plan["certificates"], "certificates", ("profile",))
    _choice(certificates["profile"], "certificates.profile", ("public-production",))
    routing = _mapping(plan["canonicalEndpointRouting"], "canonicalEndpointRouting", ("mode",))
    _choice(routing["mode"], "canonicalEndpointRouting.mode", ("internal-traefik", "public-dns"))
    endpoints = _mapping(plan["endpoints"], "endpoints", (), tuple(BROWSER_DEPENDENCIES))
    if boundary == "client-network" and default == "public":
        raise PlanError("access.default: public is forbidden under client-network, even with overrides")

    levels = {}
    grants = {}
    dependencies = {}
    warnings = []
    for name, endpoint in endpoints.items():
        path = f"endpoints.{name}"
        features = OPTIONAL_BROWSER_DEPENDENCIES.get(name, {})
        endpoint = _mapping(
            endpoint, path, ("features",) if features else (), ("level", "devices")
        )
        level = _choice(endpoint.get("level", default), f"{path}.level", LEVELS)
        if boundary == "client-network" and level == "public":
            raise PlanError(f"{path}.level: public is forbidden under client-network")
        levels[name] = level
        if level == "restricted":
            devices = endpoint.get("devices")
            if type(devices) is not list or any(
                type(device) is not str or not device.strip() or device != device.strip()
                for device in devices
            ):
                raise PlanError(f"{path}.devices: required list of nonempty, unpadded device ID strings")
            if len(devices) != len(set(devices)):
                raise PlanError(f"{path}.devices: duplicate device IDs")
            grants[name] = set(devices)
            if not devices:
                warnings.append(f"{path}: DENY-ALL; no admitted devices")
        elif "devices" in endpoint:
            raise PlanError(f"{path}.devices: allowed only for a restricted endpoint")
        dependencies[name] = list(BROWSER_DEPENDENCIES[name])
        if features:
            selected = _mapping(endpoint["features"], f"{path}.features", tuple(features))
            for feature, target in features.items():
                if type(selected[feature]) is not bool:
                    raise PlanError(f"{path}.features.{feature}: expected an explicit boolean")
                if selected[feature]:
                    dependencies[name].append(target)

    errors = []
    for source, targets in dependencies.items():
        for target in targets:
            chain = f"{source} -> {target}"
            if target not in levels:
                errors.append(f"{chain}: required endpoint is missing from plan")
                continue
            source_level, target_level = levels[source], levels[target]
            if source_level == "public" and target_level != "public":
                errors.append(f"{chain}: public source requires public target; target is {target_level}")
            elif source_level == "internal" and target_level == "restricted":
                errors.append(f"{chain}: internal source requires internal or public target, not restricted")
            elif source_level == "restricted":
                if target_level == "internal" and boundary == "internet":
                    errors.append(
                        f"{chain}: restricted source may include off-network devices under internet; "
                        "internal target reachability is not established"
                    )
                elif target_level == "restricted":
                    missing = grants[source] - grants[target]
                    if missing:
                        errors.append(
                            f"{chain}: restricted source device IDs must be a subset of target IDs; "
                            f"{len(missing)} source device(s) not admitted by target"
                        )
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="explicit normalized YAML access plan (not Helm/Kubernetes values)")
    arguments = parser.parse_args(argv)
    try:
        errors, warnings = validate_plan(load_plan(arguments.plan))
    except PlanError as error:
        errors, warnings = [str(error)], []
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        print("Access plan invalid; planning only, no changes made.", file=sys.stderr)
        return 1
    print("Access plan valid; planning only. Runtime enforcement/DNS not verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
