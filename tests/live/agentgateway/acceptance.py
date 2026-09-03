#!/usr/bin/env python3
"""Run explicit live AgentGateway/model-path acceptance checks.

This command contacts a Kubernetes API and the configured AgentGateway only when
all of these environment variables are present and valid:

* LIVE_ACCEPTANCE_CONFIRM=I_CONFIRM_LIVE_AGENTGATEWAY_ACCEPTANCE
* LIVE_ACCEPTANCE_EXPECTED_CONTEXT=<exact kubectl context>
* LIVE_ACCEPTANCE_EXPECTED_CLIENT=<exact stack identity data.client>
* LIVE_ACCEPTANCE_AGENTGATEWAY_URL=https://<agentgateway-host>
* LIVE_ACCEPTANCE_MODEL_ID=<public AgentGateway model ID>
* LIVE_ACCEPTANCE_API_KEY=<API key; environment only, never printed>

Invoke from the base repository with ``make live-acceptance``. The command reads
only the current kubectl context, the non-secret
``flux-system/neurwerk-stack-identity`` ConfigMap, and the non-secret
``infra-agentgateway/client-values`` ConfigMap that supplies the effective
AgentGateway hostname. It never reads Secrets or mutates Kubernetes resources.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Mapping


CONFIRMATION = "I_CONFIRM_LIVE_AGENTGATEWAY_ACCEPTANCE"
CONFIGMAP_NAME = "neurwerk-stack-identity"
CONFIGMAP_NAMESPACE = "flux-system"
GATEWAY_VALUES_CONFIGMAP_NAME = "client-values"
GATEWAY_NAMESPACE = "infra-agentgateway"
GATEWAY_VALUES_KEY = "values.yaml"
KUBECTL_TIMEOUT_SECONDS = 8
HTTP_TIMEOUT_SECONDS = 75
MAX_RESPONSE_BYTES = 1_048_576
PRESIDIO_CODES = frozenset({"P00", "P01", "P02", "P03"})
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
API_KEY_RE = re.compile(r"^[\x21-\x7e]{1,4096}$")
HOSTNAME_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
)
SYNTHETIC_NAME = "Jane Doe"
OPAQUE_CHAT_REASONING_FIELDS = frozenset(
    {
        "reasoning_content",
        "reasoning",
        "reasoning_details",
        "thinking_blocks",
        "reasoning_signature",
    }
)


class AcceptanceError(RuntimeError):
    """Report a safe acceptance failure without response bodies or credentials."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        """Never forward an API key to a redirected destination."""
        return None


_urlopen = urllib.request.build_opener(_NoRedirectHandler()).open


@dataclass(frozen=True)
class Config:
    context: str
    client: str
    base_url: str
    hostname: str
    model_id: str
    api_key: str


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Message
    body: bytes


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "")
    if not value or value != value.strip():
        raise AcceptanceError(f"{name} must be supplied without surrounding whitespace")
    return value


def load_config(environ: Mapping[str, str]) -> Config:
    """Validate every static guard before kubectl or HTTP can run."""
    if environ.get("LIVE_ACCEPTANCE_CONFIRM") != CONFIRMATION:
        raise AcceptanceError(
            f"LIVE_ACCEPTANCE_CONFIRM must equal exactly {CONFIRMATION}"
        )
    context = _required(environ, "LIVE_ACCEPTANCE_EXPECTED_CONTEXT")
    client = _required(environ, "LIVE_ACCEPTANCE_EXPECTED_CLIENT")
    base_url = _required(environ, "LIVE_ACCEPTANCE_AGENTGATEWAY_URL")
    model_id = _required(environ, "LIVE_ACCEPTANCE_MODEL_ID")
    api_key = _required(environ, "LIVE_ACCEPTANCE_API_KEY")

    try:
        parsed = urllib.parse.urlsplit(base_url)
        port = parsed.port
    except ValueError as exc:
        raise AcceptanceError("LIVE_ACCEPTANCE_AGENTGATEWAY_URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is not None and not 1 <= port <= 65535
    ):
        raise AcceptanceError(
            "LIVE_ACCEPTANCE_AGENTGATEWAY_URL must be an HTTPS origin without "
            "credentials, path, query, or fragment"
        )
    if not MODEL_ID_RE.fullmatch(model_id):
        raise AcceptanceError("LIVE_ACCEPTANCE_MODEL_ID has an invalid format")
    if not API_KEY_RE.fullmatch(api_key):
        raise AcceptanceError("LIVE_ACCEPTANCE_API_KEY must contain only visible ASCII")
    return Config(
        context,
        client,
        base_url.rstrip("/"),
        parsed.hostname.lower(),
        model_id,
        api_key,
    )


def _run_kubectl(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=KUBECTL_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise AcceptanceError("kubectl failed or timed out; output redacted") from exc
    if result.returncode != 0:
        raise AcceptanceError("kubectl failed; output redacted")
    return result.stdout


def _parse_gateway_hostname(values_yaml: str) -> str:
    """Parse only infraAgentgatewayWrapper.hostname from a strict YAML subset."""
    if "\t" in values_yaml or "\0" in values_yaml:
        raise AcceptanceError("AgentGateway client-values hostname is invalid")
    in_section = False
    sections = 0
    hostname_values: list[str] = []
    for line in values_yaml.splitlines():
        if line == "infraAgentgatewayWrapper:":
            sections += 1
            in_section = True
            continue
        if in_section and line and not line[0].isspace() and not line.startswith("#"):
            in_section = False
        if not in_section:
            continue
        match = re.fullmatch(r"  hostname:\s*(\S(?:.*\S)?)\s*", line)
        if match:
            hostname_values.append(match.group(1))
    if sections != 1 or len(hostname_values) != 1:
        raise AcceptanceError("AgentGateway client-values must contain one hostname")
    scalar = hostname_values[0]
    if scalar.startswith('"'):
        try:
            hostname = json.loads(scalar)
        except json.JSONDecodeError as exc:
            raise AcceptanceError("AgentGateway client-values hostname is invalid") from exc
    else:
        hostname = scalar
    if not isinstance(hostname, str):
        raise AcceptanceError("AgentGateway client-values hostname is invalid")
    hostname = hostname.lower()
    if len(hostname) > 253 or not HOSTNAME_RE.fullmatch(hostname):
        raise AcceptanceError("AgentGateway client-values hostname is invalid")
    return hostname


def verify_cluster_identity(config: Config) -> None:
    """Verify context, client identity, and its effective Gateway hostname."""
    current_context = _run_kubectl(["kubectl", "config", "current-context"]).strip()
    if current_context != config.context:
        raise AcceptanceError("current kubectl context does not match the expected context")

    raw_identity = _run_kubectl(
        [
            "kubectl",
            "--context",
            config.context,
            "--request-timeout=5s",
            "-n",
            CONFIGMAP_NAMESPACE,
            "get",
            "configmap",
            CONFIGMAP_NAME,
            "-o",
            "json",
        ]
    )
    try:
        identity = json.loads(raw_identity)
    except json.JSONDecodeError as exc:
        raise AcceptanceError("stack identity ConfigMap returned invalid JSON") from exc
    metadata = identity.get("metadata") if isinstance(identity, dict) else None
    data = identity.get("data") if isinstance(identity, dict) else None
    if (
        not isinstance(identity, dict)
        or identity.get("kind") != "ConfigMap"
        or not isinstance(metadata, dict)
        or metadata.get("name") != CONFIGMAP_NAME
        or metadata.get("namespace") != CONFIGMAP_NAMESPACE
        or not isinstance(data, dict)
        or data.get("client") != config.client
    ):
        raise AcceptanceError("stack identity ConfigMap does not match the expected client")

    raw_values_configmap = _run_kubectl(
        [
            "kubectl",
            "--context",
            config.context,
            "--request-timeout=5s",
            "-n",
            GATEWAY_NAMESPACE,
            "get",
            "configmap",
            GATEWAY_VALUES_CONFIGMAP_NAME,
            "-o",
            "json",
        ]
    )
    try:
        values_configmap = json.loads(raw_values_configmap)
    except json.JSONDecodeError as exc:
        raise AcceptanceError("AgentGateway client-values ConfigMap returned invalid JSON") from exc
    values_metadata = (
        values_configmap.get("metadata") if isinstance(values_configmap, dict) else None
    )
    values_data = values_configmap.get("data") if isinstance(values_configmap, dict) else None
    if (
        not isinstance(values_configmap, dict)
        or values_configmap.get("kind") != "ConfigMap"
        or not isinstance(values_metadata, dict)
        or values_metadata.get("name") != GATEWAY_VALUES_CONFIGMAP_NAME
        or values_metadata.get("namespace") != GATEWAY_NAMESPACE
        or not isinstance(values_data, dict)
        or not isinstance(values_data.get(GATEWAY_VALUES_KEY), str)
    ):
        raise AcceptanceError("AgentGateway client-values ConfigMap has an invalid shape")
    configured_hostname = _parse_gateway_hostname(values_data[GATEWAY_VALUES_KEY])
    if configured_hostname != config.hostname:
        raise AcceptanceError("AgentGateway URL hostname does not match client-values")


def _read_bounded(response) -> bytes:  # noqa: ANN001
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise AcceptanceError("HTTP response exceeded the size limit; body redacted")
    return body


def _request(config: Config, payload: dict, *, api_key: str, spoof: bool = False) -> HttpResult:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Accept": "text/event-stream" if payload.get("stream") else "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "neurwerk-live-acceptance/1",
    }
    if spoof:
        permission = f"model:{config.model_id}:invoke"
        headers.update(
            {
                "x-auth-user": "spoofed-live-acceptance",
                "x-user-id": "spoofed-live-acceptance",
                "x-auth-app": "agentgateway",
                "x-auth-permissions": f"llm:invoke,{permission}",
                "x-agentgateway-permissions": f"llm:invoke,{permission}",
            }
        )
    request = urllib.request.Request(
        f"{config.base_url}/v1/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        response = _urlopen(request, timeout=HTTP_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        exc.close()
        return HttpResult(exc.code, exc.headers, b"")
    except (OSError, urllib.error.URLError) as exc:
        raise AcceptanceError("HTTP request failed; response body redacted") from exc
    with response:
        status = response.getcode()
        response_body = _read_bounded(response) if 200 <= status < 300 else b""
        return HttpResult(status, response.headers, response_body)


def _presidio_code(result: HttpResult) -> str:
    values = result.headers.get_all("x-presidio-code", [])
    if len(values) != 1 or values[0].strip() not in PRESIDIO_CODES:
        raise AcceptanceError("response must contain exactly one documented x-presidio-code")
    return values[0].strip()


def _require_content_type(result: HttpResult, expected: str) -> None:
    values = result.headers.get_all("Content-Type", [])
    media_types = [value.split(";", 1)[0].strip().lower() for value in values]
    if len(media_types) != 1 or media_types[0] != expected:
        raise AcceptanceError(f"response Content-Type must be {expected}; body redacted")


def _success_json(result: HttpResult) -> dict:
    if not 200 <= result.status < 300:
        raise AcceptanceError(f"request returned HTTP {result.status}; response body redacted")
    _require_content_type(result, "application/json")
    _presidio_code(result)
    try:
        payload = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("successful response was not valid JSON; body redacted") from exc
    if not isinstance(payload, dict) or not payload:
        raise AcceptanceError("successful response JSON was empty; body redacted")
    return payload


def validate_nonstream(result: HttpResult) -> dict:
    payload = _success_json(result)
    _assistant_content(payload)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not any(
        isinstance(choice, dict)
        and isinstance(choice.get("finish_reason"), str)
        and bool(choice["finish_reason"])
        for choice in choices
    ):
        raise AcceptanceError("completion response had no choice finish_reason; body redacted")
    return payload


def _assistant_content(payload: dict) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AcceptanceError("completion response had no assistant content; body redacted") from exc
    if not isinstance(content, str) or not content.strip():
        raise AcceptanceError("completion response had empty assistant content; body redacted")
    return content


def _reject_placeholder_syntax(body: bytes, response_kind: str) -> None:
    if b"<REV_" in body or b"<ENCRYPTED_" in body:
        raise AcceptanceError(
            f"{response_kind} response leaked placeholder syntax; body redacted"
        )


def _reject_nonopaque_placeholder_syntax(payload: object, response_kind: str) -> None:
    """Reject aliases outside exact provider-opaque Chat reasoning fields."""
    stack: list[tuple[tuple[str | int, ...], object]] = [((), payload)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, str):
            _reject_placeholder_syntax(value.encode(), response_kind)
            continue
        if isinstance(value, list):
            stack.extend(((*path, index), item) for index, item in enumerate(value))
            continue
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            if isinstance(key, str):
                _reject_placeholder_syntax(key.encode(), response_kind)
            if (
                len(path) == 3
                and path[0] == "choices"
                and isinstance(path[1], int)
                and path[2] == "message"
                and key in OPAQUE_CHAT_REASONING_FIELDS
            ):
                continue
            stack.append(((*path, key), item))


def _validate_pii_report(content: str) -> None:
    """Require one aggregate report without inspecting or logging response values."""
    if content.count("PII Engine Notice") != 1 or content.count(
        "| Entity | Request | Response |"
    ) != 1:
        raise AcceptanceError("synthetic PII response did not contain exactly one report")
    if "| Person Name |" not in content or "`reversible_replace`" not in content:
        raise AcceptanceError("synthetic PII report omitted the expected aggregate action row")
    if re.search(r"\| [1-9][0-9]* restored \|", content) is None:
        raise AcceptanceError("synthetic PII report omitted response restoration counts")


def _sse_events(body: bytes) -> list[str | None]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AcceptanceError("stream response was not UTF-8; body redacted") from exc
    _reject_placeholder_syntax(body, "stream")
    events: list[str | None] = []
    frame: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            if frame:
                data_lines = [item[5:].lstrip(" ") for item in frame if item.startswith("data:")]
                events.append("\n".join(data_lines) if data_lines else None)
                frame = []
            continue
        frame.append(line)
    if frame:
        data_lines = [item[5:].lstrip(" ") for item in frame if item.startswith("data:")]
        events.append("\n".join(data_lines) if data_lines else None)
    return events


def validate_stream(result: HttpResult) -> None:
    if not 200 <= result.status < 300:
        raise AcceptanceError(f"stream returned HTTP {result.status}; response body redacted")
    _require_content_type(result, "text/event-stream")
    _presidio_code(result)
    events = _sse_events(result.body)
    content: list[str] = []
    json_events: list[dict] = []
    usage_indexes: list[int] = []
    finish_reason_seen = False
    done_count = 0
    done_seen = False
    for event in events:
        if done_seen:
            raise AcceptanceError("stream contained an event after [DONE]; body redacted")
        if event is None:
            continue
        if event == "[DONE]":
            done_count += 1
            done_seen = True
            continue
        try:
            payload = json.loads(event)
        except json.JSONDecodeError as exc:
            raise AcceptanceError("stream contained invalid JSON; body redacted") from exc
        if not isinstance(payload, dict):
            raise AcceptanceError("stream event was not a JSON object; body redacted")
        json_events.append(payload)
        if isinstance(payload.get("usage"), dict):
            usage_indexes.append(len(json_events) - 1)
        choices = payload.get("choices", [])
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                finish_reason_seen = finish_reason_seen or (
                    isinstance(choice.get("finish_reason"), str)
                    and bool(choice["finish_reason"])
                )
                if isinstance(choice.get("delta"), dict):
                    chunk = choice["delta"].get("content")
                    if isinstance(chunk, str):
                        content.append(chunk)
    usage_choices = json_events[-1].get("choices") if usage_indexes else None
    usage_is_separate = usage_choices == []
    usage_is_coalesced = isinstance(usage_choices, list) and any(
        isinstance(choice, dict)
        and isinstance(choice.get("finish_reason"), str)
        and bool(choice["finish_reason"])
        for choice in usage_choices
    )
    usage_is_final = (
        len(usage_indexes) == 1
        and usage_indexes[0] == len(json_events) - 1
        and (usage_is_separate or usage_is_coalesced)
    )
    if (
        not "".join(content).strip()
        or not finish_reason_seen
        or not usage_is_final
        or done_count != 1
    ):
        raise AcceptanceError(
            "stream requires content, finish_reason, one final usage event, and one [DONE]; "
            "body redacted"
        )


def _completion(config: Config, prompt: str, *, stream: bool = False) -> dict:
    payload = {
        "model": config.model_id,
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}
    return payload


def run_acceptance(config: Config) -> None:
    normal = _request(
        config,
        _completion(config, "Reply with a short, nonempty greeting."),
        api_key=config.api_key,
    )
    validate_nonstream(normal)
    print("PASS non-stream completion")

    streamed = _request(
        config,
        _completion(config, "Reply with a short, nonempty greeting.", stream=True),
        api_key=config.api_key,
    )
    validate_stream(streamed)
    print("PASS streaming completion")

    pii = _request(
        config,
        _completion(config, f"Repeat this exact synthetic name and nothing else: {SYNTHETIC_NAME}"),
        api_key=config.api_key,
    )
    pii_payload = validate_nonstream(pii)
    _reject_nonopaque_placeholder_syntax(pii_payload, "PII")
    pii_content = _assistant_content(pii_payload)
    if _presidio_code(pii) == "P00":
        raise AcceptanceError("synthetic PII request returned P00; response body redacted")
    if SYNTHETIC_NAME not in pii_content:
        raise AcceptanceError("synthetic name was not reversed in the response; body redacted")
    _validate_pii_report(pii_content)
    print("PASS synthetic PII round-trip")

    invalid_key = f"invalid-live-acceptance-{secrets.token_urlsafe(24)}"
    denied = _request(
        config,
        _completion(config, "This invalid credential must be denied."),
        api_key=invalid_key,
    )
    if denied.status not in {401, 403}:
        raise AcceptanceError(
            f"invalid API key was not denied (HTTP {denied.status}); response body redacted"
        )
    print("PASS invalid API key denial")

    spoofed = _request(
        config,
        _completion(config, "Spoofed identity metadata must not authorize this request."),
        api_key=invalid_key,
        spoof=True,
    )
    if spoofed.status not in {401, 403}:
        raise AcceptanceError(
            "spoofed identity metadata with invalid key was not denied "
            f"(HTTP {spoofed.status}); response body redacted"
        )
    print("PASS spoofed metadata denial")


def main(environ: Mapping[str, str] | None = None) -> int:
    try:
        config = load_config(os.environ if environ is None else environ)
        verify_cluster_identity(config)
        print("PASS expected Kubernetes context, client identity, and Gateway hostname")
        run_acceptance(config)
    except AcceptanceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
