"""Offline application access plan contract and CLI tests."""

from __future__ import annotations

import copy
import importlib.util
import itertools
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_application_access.py"
SPEC = importlib.util.spec_from_file_location("check_application_access", SCRIPT)
assert SPEC and SPEC.loader
access = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(access)


def plan(endpoints: dict | None = None, boundary: str = "internet") -> dict:
    return {
        "access": {"boundary": boundary, "default": "internal"},
        "certificates": {"profile": "public-production"},
        "canonicalEndpointRouting": {"mode": "internal-traefik"},
        "endpoints": endpoints if endpoints is not None else {},
    }


def endpoint(level: str, devices: tuple[str, ...] = ("device-a",)) -> dict:
    return {"level": level, **({"devices": list(devices)} if level == "restricted" else {})}


class ApplicationAccessTest(unittest.TestCase):
    def test_example_and_no_mutation(self) -> None:
        value = access.load_plan(ROOT / "tests/platform/application-access.example.yaml")
        before = copy.deepcopy(value)
        self.assertEqual(access.validate_plan(value), ([], []))
        self.assertEqual(value, before)

    def test_independent_routing_certificates_and_default_inheritance(self) -> None:
        for boundary, mode, level in itertools.product(
            ("internet", "client-network"), ("internal-traefik", "public-dns"), access.LEVELS
        ):
            with self.subTest(boundary=boundary, mode=mode, level=level):
                value = plan({"keycloak": endpoint(level)}, boundary)
                value["canonicalEndpointRouting"]["mode"] = mode
                value["access"]["default"] = level
                del value["endpoints"]["keycloak"]["level"]
                if boundary == "client-network" and level == "public":
                    with self.assertRaisesRegex(access.PlanError, "public is forbidden"):
                        access.validate_plan(value)
                else:
                    self.assertEqual(access.validate_plan(value), ([], []))

    def test_defaults_never_select_endpoints_or_dependencies(self) -> None:
        for level in access.LEVELS:
            value = plan()
            value["access"]["default"] = level
            self.assertEqual(access.validate_plan(value), ([], []))
        for name in ("keycloak", "librechat-files", "langfuse", "agentgateway"):
            self.assertEqual(access.validate_plan(plan({name: {}})), ([], []))
        value = plan({"studio": {}})
        self.assertIn("studio -> keycloak: required endpoint is missing", access.validate_plan(value)[0][0])
        value = plan({"keycloak": {}}, "client-network")
        value["access"]["default"] = "public"
        value["endpoints"]["keycloak"]["level"] = "internal"
        with self.assertRaisesRegex(access.PlanError, "even with overrides"):
            access.validate_plan(value)

    def test_directional_level_matrix(self) -> None:
        for boundary, source, target in itertools.product(
            ("internet", "client-network"), access.LEVELS, access.LEVELS
        ):
            with self.subTest(boundary=boundary, source=source, target=target):
                value = plan({"studio": endpoint(source), "keycloak": endpoint(target)}, boundary)
                if boundary == "client-network" and "public" in (source, target):
                    with self.assertRaisesRegex(access.PlanError, "public is forbidden"):
                        access.validate_plan(value)
                    continue
                allowed = (
                    (source == "public" and target == "public")
                    or (source == "internal" and target in ("internal", "public"))
                    or (source == "restricted" and (
                        target in ("public", "restricted") or boundary == "client-network"
                    ))
                )
                errors, warnings = access.validate_plan(value)
                self.assertEqual(not errors, allowed, errors)
                self.assertEqual(warnings, [])
                for error in errors:
                    self.assertIn("studio -> keycloak:", error)

    def test_restricted_actual_device_subsets_and_deny_all(self) -> None:
        cases = [
            (("device-a",), ("device-a",), True),
            (("device-a",), ("device-b", "device-a"), True),
            (("device-a", "device-b"), ("device-a",), False),
            (("device-a",), ("device-b",), False),
            (("device-a",), ("Device-a",), False),
            (("device-a",), (), False),
            ((), ("device-a",), True),
            ((), (), True),
        ]
        for boundary, (source, target, allowed) in itertools.product(
            ("internet", "client-network"), cases
        ):
            with self.subTest(boundary=boundary, source=source, target=target):
                value = plan({
                    "studio": endpoint("restricted", source),
                    "keycloak": endpoint("restricted", target),
                }, boundary)
                before = copy.deepcopy(value)
                errors, warnings = access.validate_plan(value)
                self.assertEqual(not errors, allowed, errors)
                self.assertEqual(len(warnings), int(not source) + int(not target))
                for name, devices in (("studio", source), ("keycloak", target)):
                    if not devices:
                        self.assertIn(f"endpoints.{name}: DENY-ALL; no admitted devices", warnings)
                if errors:
                    self.assertIn("studio -> keycloak: restricted source device IDs must be a subset", errors[0])
                self.assertEqual(value, before)
        errors, warnings = access.validate_plan(plan({"studio": endpoint("restricted", ())}))
        self.assertIn("required endpoint is missing", errors[0])
        self.assertEqual(len(warnings), 1)

    def test_mandatory_dependencies_and_admin_chain(self) -> None:
        for name, target in (
            ("librechat", "keycloak"), ("studio", "keycloak"), ("forgejo", "keycloak"),
            ("librechat-admin", "librechat"), ("librechat-admin", "keycloak"),
        ):
            with self.subTest(name=name, target=target):
                endpoints = {"keycloak": {}, "librechat": {"features": {"files": False}}, name: {}}
                if name == "librechat":
                    endpoints[name] = {"features": {"files": False}}
                del endpoints[target]
                errors, _ = access.validate_plan(plan(endpoints))
                self.assertTrue(any(f"{name} -> {target}: required endpoint is missing" in e for e in errors))
        value = plan({
            "librechat-admin": endpoint("restricted"),
            "librechat": {**endpoint("restricted"), "features": {"files": True}},
            "keycloak": endpoint("restricted"),
            "librechat-files": endpoint("restricted", ("device-b",)),
        })
        errors, _ = access.validate_plan(value)
        self.assertEqual(len(errors), 1)
        self.assertIn("librechat -> librechat-files:", errors[0])

    def test_optional_workflows_missing_and_inaccessible_dependencies(self) -> None:
        for name, feature, target in (
            ("librechat", "files", "librechat-files"), ("dify", "consoleSSO", "keycloak")
        ):
            for enabled in (False, True):
                with self.subTest(name=name, enabled=enabled):
                    endpoints = {name: {"level": "public", "features": {feature: enabled}}}
                    if name == "librechat":
                        endpoints["keycloak"] = endpoint("public")
                    value = plan(endpoints)
                    errors, _ = access.validate_plan(value)
                    self.assertEqual(bool(errors), enabled)
                    if enabled:
                        self.assertIn(f"{name} -> {target}: required endpoint is missing", errors[0])
                    endpoints[target] = endpoint("internal")
                    errors, _ = access.validate_plan(value)
                    self.assertEqual(bool(errors), enabled)
                    endpoints[target] = endpoint("public")
                    self.assertEqual(access.validate_plan(value), ([], []))

    def test_strict_schema(self) -> None:
        # Each case changes one field of an otherwise valid plan.
        cases = [
            (("access",), None), (("access",), []), (("access", "boundary"), True),
            (("access", "boundary"), "lan"), (("access", "default"), "private"),
            (("access", "extra"), "withheld-value"),
            (("certificates", "profile"), "private-ca"),
            (("certificates", "profile"), "public-staging"),
            (("certificates", "extra"), True),
            (("canonicalEndpointRouting", "mode"), "internal"),
            (("canonicalEndpointRouting", "extra"), True),
            (("endpoints",), []), (("endpoints",), None),
            (("endpoints", "backend"), {}), (("endpoints", "studio"), None),
            (("endpoints", "studio"), {"level": None}),
            (("endpoints", "studio"), {"enabled": False}),
            (("endpoints", "studio"), {"features": {}}),
            (("endpoints", "studio"), {"groups": ["device-a"]}),
            (("endpoints", "studio"), {"devices": []}),
            (("endpoints", "studio"), {"level": "restricted"}),
            (("kind",), "Secret"), (("apiVersion",), "v1"),
        ]
        for devices in (None, "device-a", [True], [1], [{}], [""], [" "], [" device-a"], ["device-a"] * 2):
            cases.append((("endpoints", "studio"), {"level": "restricted", "devices": devices}))
        for name, feature in (("librechat", "files"), ("dify", "consoleSSO")):
            for features in (None, {}, [], {feature: "false"}, {feature: 0}, {feature: None},
                             {feature: False, "unknown": True}):
                cases.append((("endpoints", name), {"features": features}))
            cases.append((("endpoints", name), {}))
        for path, replacement in cases:
            with self.subTest(path=path, replacement=replacement):
                value = plan()
                parent = value
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = replacement
                with self.assertRaises(access.PlanError):
                    access.validate_plan(value)
        for value in (None, [], True, "text", 1, {1: {}}, {"kind": "Secret"}):
            with self.subTest(value=value), self.assertRaises(access.PlanError):
                access.validate_plan(value)
        for field in plan():
            value = plan()
            del value[field]
            with self.subTest(missing=field), self.assertRaises(access.PlanError):
                access.validate_plan(value)

    def test_cli_safe_diagnostics_and_read_only_input(self) -> None:
        valid = yaml.safe_dump(plan())
        cases = [
            (valid, 0, "Access plan valid; planning only. Runtime enforcement/DNS not verified."),
            (yaml.safe_dump(plan({"keycloak": endpoint("restricted", ())})), 0, "DENY-ALL; no admitted devices"),
            (yaml.safe_dump(plan({"studio": {}})), 1, "studio -> keycloak: required endpoint is missing"),
            ("access: [withheld-value", 1, "invalid or unsupported YAML"),
            ("access: {}\naccess: withheld-value\n", 1, "duplicate YAML mapping key"),
            ("access: {boundary: internet, boundary: withheld-value}", 1, "duplicate YAML mapping key"),
            ("withheld-value: true", 1, "unknown field"),
            ("? [withheld-value]\n: true", 1, "mapping keys must be strings"),
            ("access: &value {default: withheld-value}\nendpoints: *value", 1, "aliases are not supported"),
            ("access: {<<: {default: withheld-value}}", 1, "invalid or unsupported YAML"),
            ("!!python/object/apply:os.system ['withheld-value']", 1, "explicit YAML tags are not supported"),
            ("!!bool withheld-value", 1, "explicit YAML tags are not supported"),
            ("!!timestamp withheld-value", 1, "explicit YAML tags are not supported"),
            ("!!map [withheld-value]", 1, "explicit YAML tags are not supported"),
            ("---\n{}\n---\nwithheld-value", 1, "invalid or unsupported YAML"),
            ("null", 1, "expected a mapping"), ("", 1, "expected a mapping"),
            ("[" * 1500, 1, "invalid or unsupported YAML"),
            ("access: 1:" + "00:" * 200 + "00.0", 1, "invalid or unsupported YAML"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.yaml"
            for text, code, message in cases:
                with self.subTest(message=message, code=code):
                    path.write_text(text, encoding="utf-8")
                    before = path.read_bytes()
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True
                    )
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertIn(message, result.stdout + result.stderr)
                    self.assertNotIn("withheld-value", result.stdout + result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    if code:
                        self.assertEqual(result.stdout, "")
                    self.assertEqual(path.read_bytes(), before)
            path.write_bytes(b"\xff")
            with self.assertRaisesRegex(access.PlanError, "cannot read plan as UTF-8"):
                access.load_plan(path)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(Path(directory) / "absent.yaml")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("cannot read plan as UTF-8", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
