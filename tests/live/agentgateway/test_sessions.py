"""Native OTLP session grouping and streaming; uses synthetic trusted metadata."""

import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_accounting import SHA256, binding_values, port, post, render, resource


class SessionTest(unittest.TestCase):
    def test_verified_principal_scopes_agent_sessions_without_buffering_sse(self):
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

        binary = Path(os.environ.get("AGENTGATEWAY_BIN", ""))
        self.assertTrue(binary.is_file() and os.access(binary, os.X_OK), "SETUP: supply AGENTGATEWAY_BIN")
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), SHA256)
        raw = resource(render("agentgateway", binding_values()), "AgentgatewayParameters")["spec"]["rawConfig"]
        same = {"x-session-id": "session-alpha"}
        cases = [
            ("first", "person-a", same, "identity"),
            ("repeat", "person-a", same, "identity"),
            ("other-user", "person-b", same, "identity"),
            ("other-session", "person-a", {"x-session-id": "session-bravo"}, "identity"),
            ("claude", "person-a", {"x-claude-code-session-id": "session-alpha"}, "identity"),
            ("codex", "person-a", {"user-agent": "codex_cli_rs/1.0", "session-id": "session-alpha"}, "identity"),
            ("admission-session", "person-a", same, "admission"),
            ("missing", "person-a", {}, "identity"),
            ("empty", "person-a", {"x-session-id": ""}, "identity"),
            ("short", "person-a", {"x-session-id": "short"}, "identity"),
            ("invalid", "person-a", {"x-session-id": "invalid session"}, "identity"),
            ("admission-fallback", "person-a", {}, "admission"),
            ("untrusted", None, {**same, "x-user-id": "person-a", "x-auth-user": "person-a"}, "identity"),
            ("anonymous", None, {**same, "x-user-id": "person-a"}, None),
            ("stream", "person-a", same, "identity"),
        ]
        request_ids = {name: str(uuid.uuid4()) for name, _, _, _ in cases}
        # Inject the post-authentication metadata, not caller-controlled identity.
        metadata = {}
        for name, principal, _, source in cases:
            identity = {"request_id": request_ids[name]}
            if principal is not None:
                identity["principal_id"] = principal
            metadata[name] = {f"neurwerk_{source}": identity} if source else {}
        spans, errors = [], []
        release, finished = threading.Event(), threading.Event()

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    request = json.loads(self.rfile.read(int(self.headers["content-length"])))
                    if request.get("stream"):
                        self.send_response(200)
                        self.send_header("content-type", "text/event-stream")
                        self.send_header("connection", "close")
                        self.end_headers()
                        event = {"id": "fixture", "object": "chat.completion.chunk", "created": 1,
                                 "model": "example-chat", "choices": [{"index": 0,
                                 "delta": {"role": "assistant", "content": "first"}, "finish_reason": None}]}
                        self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                        self.wfile.flush()
                        if not release.wait(10):
                            raise TimeoutError("first SSE event was held until EOF")
                        event["choices"][0].update(delta={}, finish_reason="stop")
                        self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n")
                        self.wfile.flush()
                        finished.set()
                    else:
                        data = {"id": "fixture", "object": "chat.completion", "created": 1,
                                "model": "example-chat", "choices": [{"index": 0,
                                "message": {"role": "assistant", "content": "safe"}, "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
                        encoded = json.dumps(data).encode()
                        self.send_response(200)
                        self.send_header("content-type", "application/json")
                        self.send_header("content-length", str(len(encoded)))
                        self.end_headers()
                        self.wfile.write(encoded)
                except Exception as error:
                    errors.append(repr(error))
                finally:
                    self.close_connection = True

        class Collector(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                payload = ExportTraceServiceRequest.FromString(self.rfile.read(int(self.headers["content-length"])))
                for resources in payload.resource_spans:
                    for scope in resources.scope_spans:
                        spans.extend(scope.spans)
                self.send_response(200)
                self.send_header("content-type", "application/x-protobuf")
                self.send_header("content-length", "0")
                self.end_headers()

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in [upstream, collector]]
        for thread in threads:
            thread.start()
        process, connection = None, None
        try:
            with tempfile.TemporaryDirectory(prefix="gateway-sessions-") as temporary:
                gateway = port()
                tracing = copy.deepcopy(raw["config"]["tracing"])
                tracing.update(otlpEndpoint=f"http://127.0.0.1:{collector.server_port}/v1/traces", otlpProtocol="http")
                tracing["fields"]["add"]["fixture.case"] = 'request.headers["x-fixture-case"]'
                config = {
                    "config": {"adminAddr": "off", "statsAddr": "off", "readinessAddr": "off", "tracing": tracing},
                    "gateways": {"api": {"port": gateway, "bindAddress": "127.0.0.1", "transformations": {
                        "conditional": [{"condition": f'request.headers["x-fixture-case"] == {json.dumps(name)}',
                                         "request": {"metadata": {key: json.dumps(value) for key, value in fields.items()}}}
                                        for name, fields in metadata.items() if fields]}}},
                    "llm": {"gateways": "api", "models": [{"name": "example-chat", "provider": "openAI",
                            "params": {"baseUrl": f"http://127.0.0.1:{upstream.server_port}/v1"}}]},
                }
                log_path = Path(temporary) / "gateway.log"
                with log_path.open("w") as log:
                    process = subprocess.Popen([str(binary.resolve()), "-c", json.dumps(config)], stdout=log, stderr=log,
                                               env={"PATH": os.defpath, "HOME": temporary})
                    for _ in range(100):
                        self.assertIsNone(process.poll(), log_path.read_text())
                        try:
                            with socket.create_connection(("127.0.0.1", gateway), timeout=0.1):
                                break
                        except OSError:
                            time.sleep(0.05)
                    else:
                        self.fail("gateway not ready: " + log_path.read_text())
                    for name, _, headers, _ in cases:
                        body = {"model": "example-chat", "messages": [{"role": "user", "content": "safe"}]}
                        headers = {**headers, "x-fixture-case": name}
                        if name != "stream":
                            status, payload = post(gateway, "/v1/chat/completions", body, headers)
                            self.assertEqual(status, 200, payload)
                            continue
                        body["stream"] = True
                        connection = http.client.HTTPConnection("127.0.0.1", gateway, timeout=5)
                        connection.request("POST", "/v1/chat/completions", json.dumps(body),
                                           {"content-type": "application/json", **headers})
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        event = []
                        while line := response.readline():
                            event.append(line)
                            if line in (b"\n", b"\r\n"):
                                break
                        self.assertIn(b"first", b"".join(event))
                        self.assertFalse(finished.is_set(), "first SSE event was buffered until EOF")
                        release.set()
                        self.assertIn(b"[DONE]", response.read())
                    traced = {}
                    for _ in range(200):
                        for span in list(spans):
                            fields = {item.key: getattr(item.value, item.value.WhichOneof("value")) for item in span.attributes}
                            if "fixture.case" in fields:
                                traced[fields["fixture.case"]] = fields
                        if set(traced) == set(request_ids):
                            break
                        time.sleep(0.05)
                    self.assertEqual(set(traced), set(request_ids), log_path.read_text())
                    sessions = {name: fields.get("langfuse.session.id") for name, fields in traced.items()}
                    self.assertTrue(sessions["first"])
                    for name in ["repeat", "claude", "codex", "admission-session", "stream"]:
                        self.assertEqual(sessions[name], sessions["first"], name)
                    for name in ["other-user", "other-session"]:
                        self.assertTrue(sessions[name])
                        self.assertNotEqual(sessions[name], sessions["first"], name)
                    for name in ["missing", "empty", "short", "invalid", "admission-fallback", "untrusted"]:
                        self.assertEqual(sessions[name], request_ids[name], name)
                    self.assertIsNone(sessions["anonymous"])
                    for name, _, _, source in cases:
                        self.assertEqual(traced[name].get("neurwerk.request_id"), request_ids[name] if source else None)
                    self.assertFalse(errors, errors)
        finally:
            release.set()
            if connection is not None:
                connection.close()
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for server in [upstream, collector]:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
