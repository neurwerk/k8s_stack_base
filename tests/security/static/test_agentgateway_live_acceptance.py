"""Offline safety and parsing tests for live AgentGateway acceptance."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "tests/live/agentgateway/acceptance.py"
SPEC = importlib.util.spec_from_file_location("agentgateway_live_acceptance", RUNNER)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load AgentGateway live acceptance runner")
acceptance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = acceptance
SPEC.loader.exec_module(acceptance)


def valid_environment() -> dict[str, str]:
    return {
        "LIVE_ACCEPTANCE_CONFIRM": acceptance.CONFIRMATION,
        "LIVE_ACCEPTANCE_EXPECTED_CONTEXT": "acceptance-context",
        "LIVE_ACCEPTANCE_EXPECTED_CLIENT": "client_acceptance_test",
        "LIVE_ACCEPTANCE_AGENTGATEWAY_URL": "https://gateway.acceptance.test",
        "LIVE_ACCEPTANCE_MODEL_ID": "remote/acceptance",
        "LIVE_ACCEPTANCE_API_KEY": "live-secret-api-key",
    }


def headers(code: str = "P00", media_type: str = "application/json") -> Message:
    result = Message()
    result.add_header("x-presidio-code", code)
    result.add_header("Content-Type", media_type)
    return result


def result(body: dict, code: str = "P00") -> acceptance.HttpResult:
    return acceptance.HttpResult(200, headers(code), json.dumps(body).encode())


def identity_configmap(client: str = "client_acceptance_test") -> dict:
    return {
        "kind": "ConfigMap",
        "metadata": {
            "name": acceptance.CONFIGMAP_NAME,
            "namespace": acceptance.CONFIGMAP_NAMESPACE,
        },
        "data": {"client": client},
    }


def gateway_values_configmap(hostname: str = "gateway.acceptance.test") -> dict:
    return {
        "kind": "ConfigMap",
        "metadata": {
            "name": acceptance.GATEWAY_VALUES_CONFIGMAP_NAME,
            "namespace": acceptance.GATEWAY_NAMESPACE,
        },
        "data": {
            acceptance.GATEWAY_VALUES_KEY: (
                "authKeycloak:\n"
                "  hostname: auth.acceptance.test\n"
                "infraAgentgatewayWrapper:\n"
                f'  hostname: "{hostname}"\n'
                "frontendStudio:\n"
                "  enabled: true\n"
            )
        },
    }


def completion(content: str = "hello", finish_reason: str = "stop") -> dict:
    return {
        "choices": [
            {"message": {"content": content}, "finish_reason": finish_reason}
        ]
    }


def pii_content() -> str:
    return (
        f"{acceptance.SYNTHETIC_NAME}\n\n---\nPII Engine Notice\nProtected\n\n"
        "| Entity | Request | Response |\n"
        "| --- | --- | --- |\n"
        "| Person Name | `reversible_replace`: 1 detected; 1 transformed (1 unique) "
        "| 1 restored |"
    )


def valid_stream() -> bytes:
    return (
        b'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: {"choices":[],"usage":{"total_tokens":3}}\n\n'
        b"data: [DONE]\n\n"
    )


class FakeResponse:
    def __init__(self, status: int, body: bytes, response_headers: Message | None = None):
        self.status = status
        self.body = body
        self.headers = response_headers or Message()

    def getcode(self) -> int:
        return self.status

    def read(self, size: int) -> bytes:
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:  # noqa: ANN002
        return None


class AgentGatewayLiveAcceptanceTests(unittest.TestCase):
    def test_every_static_guard_fails_before_subprocess_or_http(self) -> None:
        invalid_environments = []
        for name in valid_environment():
            environment = valid_environment()
            environment.pop(name)
            invalid_environments.append((name, environment))
        environment = valid_environment()
        environment["LIVE_ACCEPTANCE_CONFIRM"] = "yes"
        invalid_environments.append(("inexact confirmation", environment))
        environment = valid_environment()
        environment["LIVE_ACCEPTANCE_AGENTGATEWAY_URL"] = "http://gateway.test"
        invalid_environments.append(("non-HTTPS URL", environment))
        environment = valid_environment()
        environment["LIVE_ACCEPTANCE_API_KEY"] = "unsafe\nkey"
        invalid_environments.append(("unsafe API key", environment))

        for label, environment in invalid_environments:
            with self.subTest(label=label), mock.patch.object(
                acceptance.subprocess, "run"
            ) as run, mock.patch.object(acceptance, "_urlopen") as urlopen:
                self.assertEqual(acceptance.main(environment), 1)
                run.assert_not_called()
                urlopen.assert_not_called()

    def test_context_mismatch_stops_before_cluster_or_http_traffic(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="other-context\n", stderr="")
        with mock.patch.object(
            acceptance.subprocess, "run", return_value=completed
        ) as run, mock.patch.object(acceptance, "_urlopen") as urlopen:
            self.assertEqual(acceptance.main(valid_environment()), 1)
        self.assertEqual(run.call_count, 1)
        urlopen.assert_not_called()

    def test_identity_uses_only_fixed_read_only_non_secret_commands(self) -> None:
        outputs = [
            subprocess.CompletedProcess([], 0, stdout="acceptance-context\n", stderr=""),
            subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(identity_configmap()), stderr=""
            ),
            subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(gateway_values_configmap()), stderr=""
            ),
        ]
        with mock.patch.object(acceptance.subprocess, "run", side_effect=outputs) as run:
            acceptance.verify_cluster_identity(acceptance.load_config(valid_environment()))
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], ["kubectl", "config", "current-context"])
        self.assertEqual(
            commands[1],
            [
                "kubectl",
                "--context",
                "acceptance-context",
                "--request-timeout=5s",
                "-n",
                "flux-system",
                "get",
                "configmap",
                "neurwerk-stack-identity",
                "-o",
                "json",
            ],
        )
        self.assertEqual(
            commands[2],
            [
                "kubectl",
                "--context",
                "acceptance-context",
                "--request-timeout=5s",
                "-n",
                "infra-agentgateway",
                "get",
                "configmap",
                "client-values",
                "-o",
                "json",
            ],
        )
        flattened = {part.lower() for command in commands for part in command}
        self.assertNotIn("secret", flattened)
        self.assertTrue(flattened.isdisjoint({"apply", "create", "delete", "edit", "patch"}))
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["timeout"], acceptance.KUBECTL_TIMEOUT_SECONDS)
            self.assertFalse(call.kwargs.get("shell", False))

    def test_client_identity_mismatch_stops_before_http(self) -> None:
        outputs = [
            subprocess.CompletedProcess([], 0, stdout="acceptance-context\n", stderr=""),
            subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(identity_configmap("wrong-client")), stderr=""
            ),
        ]
        with mock.patch.object(
            acceptance.subprocess, "run", side_effect=outputs
        ), mock.patch.object(acceptance, "_urlopen") as urlopen:
            self.assertEqual(acceptance.main(valid_environment()), 1)
        urlopen.assert_not_called()

    def test_gateway_hostname_mismatch_stops_before_http(self) -> None:
        outputs = [
            subprocess.CompletedProcess([], 0, stdout="acceptance-context\n", stderr=""),
            subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(identity_configmap()), stderr=""
            ),
            subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps(gateway_values_configmap("other.acceptance.test")),
                stderr="",
            ),
        ]
        output = io.StringIO()
        with mock.patch.object(
            acceptance.subprocess, "run", side_effect=outputs
        ), mock.patch.object(acceptance, "_urlopen") as urlopen, contextlib.redirect_stderr(
            output
        ):
            self.assertEqual(acceptance.main(valid_environment()), 1)
        self.assertIn("hostname does not match", output.getvalue())
        urlopen.assert_not_called()

    def test_gateway_hostname_parser_is_strict_and_minimal(self) -> None:
        values = gateway_values_configmap()["data"][acceptance.GATEWAY_VALUES_KEY]
        self.assertEqual(
            acceptance._parse_gateway_hostname(values), "gateway.acceptance.test"
        )
        invalid_values = (
            values.replace(
                '  hostname: "gateway.acceptance.test"\n',
                '  hostname: "gateway.acceptance.test"\n  hostname: duplicate.test\n',
            ),
            values + "infraAgentgatewayWrapper:\n  hostname: duplicate.test\n",
            values.replace('"gateway.acceptance.test"', "*gateway.acceptance.test"),
        )
        for invalid in invalid_values:
            with self.subTest(invalid=invalid), self.assertRaises(
                acceptance.AcceptanceError
            ):
                acceptance._parse_gateway_hostname(invalid)

    def test_malformed_identity_shape_fails_closed(self) -> None:
        outputs = [
            subprocess.CompletedProcess([], 0, stdout="acceptance-context\n", stderr=""),
            subprocess.CompletedProcess(
                [],
                0,
                stdout='{"kind":"ConfigMap","metadata":[],"data":[]}',
                stderr="",
            ),
        ]
        with mock.patch.object(acceptance.subprocess, "run", side_effect=outputs):
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.verify_cluster_identity(acceptance.load_config(valid_environment()))

    def test_credentials_and_failure_bodies_are_not_logged(self) -> None:
        secret = valid_environment()["LIVE_ACCEPTANCE_API_KEY"]
        config = acceptance.load_config(valid_environment())
        failures = [
            urllib.error.URLError(secret),
            FakeResponse(500, f"upstream leaked {secret}".encode()),
        ]
        for failure in failures:
            output = io.StringIO()
            patch = (
                mock.patch.object(acceptance, "_urlopen", side_effect=failure)
                if isinstance(failure, Exception)
                else mock.patch.object(acceptance, "_urlopen", return_value=failure)
            )
            with self.subTest(failure=type(failure).__name__), mock.patch.object(
                acceptance, "verify_cluster_identity"
            ), patch, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(acceptance.main(valid_environment()), 1)
            self.assertNotIn(secret, output.getvalue())

        failed = FakeResponse(500, f"upstream leaked {secret}".encode())
        with mock.patch.object(acceptance, "_urlopen", return_value=failed):
            response = acceptance._request(config, {"model": config.model_id}, api_key=secret)
            self.assertEqual(response.body, b"")

    def test_nonstream_contract_requires_json_content_finish_and_one_known_code(self) -> None:
        payload = completion()
        parsed = acceptance.validate_nonstream(result(payload))
        self.assertEqual(acceptance._assistant_content(parsed), "hello")

        duplicate_headers = headers("P00")
        duplicate_headers.add_header("x-presidio-code", "P01")
        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.validate_nonstream(
                acceptance.HttpResult(200, duplicate_headers, json.dumps(payload).encode())
            )
        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.validate_nonstream(
                acceptance.HttpResult(200, headers("P99"), json.dumps(payload).encode())
            )
        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.validate_nonstream(
                acceptance.HttpResult(
                    200,
                    headers(media_type="text/plain"),
                    json.dumps(payload).encode(),
                )
            )
        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.validate_nonstream(result(completion(finish_reason="")))

    def test_stream_contract_requires_content_finish_usage_tail_and_terminal(self) -> None:
        body = valid_stream()
        stream_headers = headers(media_type="text/event-stream; charset=utf-8")
        acceptance.validate_stream(acceptance.HttpResult(200, stream_headers, body))
        for invalid in (
            body.replace(b"data: [DONE]\n\n", b""),
            body + b"data: [DONE]\n\n",
            body + b": post-terminal-heartbeat\n\n",
            body.replace(b"hello", b"<REV_PERSON_aaaaaaaaaaaaaaaa_bbbbbbbbbbbbbbbb>"),
            body.replace(b'"usage":{"total_tokens":3}', b'"usage":null'),
            body.replace(b'"finish_reason":"stop"', b'"finish_reason":null'),
            body.replace(
                b"data: [DONE]",
                b'data: {"choices":[]}\n\ndata: [DONE]',
            ),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(acceptance.AcceptanceError):
                acceptance.validate_stream(
                    acceptance.HttpResult(200, stream_headers, invalid)
                )

        duplicate_code = headers(media_type="text/event-stream")
        duplicate_code.add_header("x-presidio-code", "P01")
        missing_code = Message()
        missing_code.add_header("Content-Type", "text/event-stream")
        for invalid_headers in (
            headers(),
            duplicate_code,
            missing_code,
            headers("P99", "text/event-stream"),
        ):
            with self.subTest(headers=invalid_headers), self.assertRaises(
                acceptance.AcceptanceError
            ):
                acceptance.validate_stream(
                    acceptance.HttpResult(200, invalid_headers, body)
                )

    def test_success_response_size_is_bounded(self) -> None:
        config = acceptance.load_config(valid_environment())
        oversized = FakeResponse(200, b"x" * (acceptance.MAX_RESPONSE_BYTES + 1))
        with mock.patch.object(acceptance, "_urlopen", return_value=oversized):
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance._request(config, {"model": config.model_id}, api_key=config.api_key)

    def test_pii_round_trip_contract_rejects_p00_and_placeholder_syntax(self) -> None:
        payload = completion(pii_content())
        pii_result = result(payload, "P02")
        parsed = acceptance.validate_nonstream(pii_result)
        self.assertEqual(acceptance._presidio_code(pii_result), "P02")
        self.assertIn(acceptance.SYNTHETIC_NAME, acceptance._assistant_content(parsed))
        acceptance._validate_pii_report(acceptance._assistant_content(parsed))
        with self.assertRaises(acceptance.AcceptanceError):
            acceptance._validate_pii_report(acceptance.SYNTHETIC_NAME)
        self.assertEqual(acceptance._presidio_code(result(payload, "P00")), "P00")
        with self.assertRaises(acceptance.AcceptanceError):
            acceptance._reject_placeholder_syntax(
                b'{"metadata":"<ENCRYPTED_PERSON_token>"}', "PII"
            )

        stream = acceptance.HttpResult(
            200,
            headers(media_type="text/event-stream"),
            valid_stream(),
        )
        with mock.patch.object(
            acceptance,
            "_request",
            side_effect=[result(payload), stream, result(payload, "P00")],
        ), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(
            acceptance.AcceptanceError
        ):
            acceptance.run_acceptance(acceptance.load_config(valid_environment()))

    def test_live_target_is_explicit_and_not_part_of_check(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        check_line = next(line for line in makefile.splitlines() if line.startswith("check:"))
        self.assertNotIn("live-acceptance", check_line)
        self.assertIn("live-acceptance:", makefile)
        self.assertIn("tests/live/agentgateway/acceptance.py", makefile)

    def test_full_suite_uses_valid_key_only_for_positive_cases_and_spoofs_reserved_headers(self) -> None:
        config = acceptance.load_config(valid_environment())
        responses = [
            FakeResponse(200, json.dumps(completion()).encode(), headers()),
            FakeResponse(
                200,
                valid_stream(),
                headers(media_type="text/event-stream"),
            ),
            FakeResponse(
                200,
                json.dumps(completion(pii_content())).encode(),
                headers("P02"),
            ),
            FakeResponse(401, b"denied"),
            FakeResponse(403, b"denied"),
        ]
        requests = []

        def open_request(request, *, timeout):  # noqa: ANN001
            self.assertEqual(timeout, acceptance.HTTP_TIMEOUT_SECONDS)
            requests.append(request)
            return responses.pop(0)

        with mock.patch.object(acceptance, "_urlopen", side_effect=open_request):
            acceptance.run_acceptance(config)
        self.assertEqual(len(requests), 5)
        for request in requests[:3]:
            self.assertEqual(request.get_header("Authorization"), f"Bearer {config.api_key}")
        stream_request = json.loads(requests[1].data)
        # AgentGateway adds include_usage after extProc request validation.
        self.assertNotIn("stream_options", stream_request)
        for request in requests[3:]:
            self.assertNotIn(config.api_key, request.get_header("Authorization"))
        spoofed = {name.lower(): value for name, value in requests[4].header_items()}
        self.assertIn("x-auth-user", spoofed)
        self.assertIn("x-auth-permissions", spoofed)
        self.assertIn("x-agentgateway-permissions", spoofed)


if __name__ == "__main__":
    unittest.main()
