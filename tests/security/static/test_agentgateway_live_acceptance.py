"""High-signal offline safety tests for live AgentGateway acceptance."""

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
    result_headers = Message()
    result_headers.add_header("x-presidio-code", code)
    result_headers.add_header("Content-Type", media_type)
    return result_headers


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


def stream(*events: str) -> bytes:
    return "".join(f"data: {event}\n\n" for event in events).encode()


def valid_stream(*, coalesced_usage: bool = False) -> bytes:
    events = [
        '{"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}',
        (
            '{"choices":[{"delta":{},"finish_reason":"stop"}],'
            '"usage":{"total_tokens":3}}'
            if coalesced_usage
            else '{"choices":[{"delta":{},"finish_reason":"stop"}]}'
        ),
    ]
    if not coalesced_usage:
        events.append('{"choices":[],"usage":{"total_tokens":3}}')
    events.append("[DONE]")
    return stream(*events)


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
    def test_completion_requests_usage_only_for_streaming(self) -> None:
        config = acceptance.load_config(valid_environment())

        nonstream = acceptance._completion(config, "hello")
        streamed = acceptance._completion(config, "hello", stream=True)

        self.assertNotIn("stream_options", nonstream)
        self.assertEqual(streamed["stream_options"], {"include_usage": True})

    def test_stream_accepts_separate_and_coalesced_final_usage(self) -> None:
        for coalesced_usage in (False, True):
            with self.subTest(coalesced_usage=coalesced_usage):
                acceptance.validate_stream(
                    acceptance.HttpResult(
                        200,
                        headers(media_type="text/event-stream"),
                        valid_stream(coalesced_usage=coalesced_usage),
                    )
                )

    def test_stream_rejects_invalid_usage_and_done_placement(self) -> None:
        cases = {
            "missing usage": stream(
                '{"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}',
                '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
                "[DONE]",
            ),
            "duplicate usage": stream(
                '{"choices":[{"delta":{"content":"hello"},"finish_reason":null}],'
                '"usage":{"total_tokens":2}}',
                '{"choices":[{"delta":{},"finish_reason":"stop"}],'
                '"usage":{"total_tokens":3}}',
                "[DONE]",
            ),
            "non-final usage": stream(
                '{"choices":[{"delta":{"content":"hello"},"finish_reason":null}],'
                '"usage":{"total_tokens":2}}',
                '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
                "[DONE]",
            ),
            "usage on unfinished choice": stream(
                '{"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}',
                '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
                '{"choices":[{"delta":{},"finish_reason":null}],'
                '"usage":{"total_tokens":3}}',
                "[DONE]",
            ),
            "duplicate DONE": valid_stream() + b"data: [DONE]\n\n",
            "event after DONE": valid_stream()
            + b'data: {"choices":[],"usage":{"total_tokens":4}}\n\n',
        }
        for name, body in cases.items():
            with self.subTest(name=name), self.assertRaises(
                acceptance.AcceptanceError
            ):
                acceptance.validate_stream(
                    acceptance.HttpResult(
                        200,
                        headers(media_type="text/event-stream"),
                        body,
                    )
                )

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
        self.assertTrue(
            flattened.isdisjoint({"apply", "create", "delete", "edit", "patch"})
        )
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
            response = acceptance._request(
                config, {"model": config.model_id}, api_key=secret
            )
            self.assertEqual(response.body, b"")

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


if __name__ == "__main__":
    unittest.main()
