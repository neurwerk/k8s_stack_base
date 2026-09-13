"""Opt-in, synthetic HTTP/SSE regression against the official Linux amd64 binary."""

import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import select
import socket
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer


SHA256 = "daca5cda76e8c5ab0c1a75912fecf2d6365095403f810db72029c49d14a37e7b"
HEADER = "x-agentgateway-auth-context"
FIRST = b"data: first\n\n"
LAST = b"data: last\n\n"
TIMEOUT = 5


class StreamingTest(unittest.TestCase):
    def test_auth_metadata_streaming(self):
        supplied = os.environ.get("AGENTGATEWAY_BIN")
        self.assertTrue(
            supplied,
            "SETUP: set AGENTGATEWAY_BIN to the verified official 1.5.0 Linux "
            "amd64 executable; see tests/live/agentgateway/README.md (no skip).",
        )
        binary = Path(supplied).resolve()
        self.assertTrue(
            binary.is_file() and os.access(binary, os.X_OK),
            "SETUP: AGENTGATEWAY_BIN must name an executable file; see README.md.",
        )
        with binary.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        self.assertEqual(digest, SHA256, "SETUP: wrong AGENTGATEWAY_BIN SHA256; not executing it")
        self.assertEqual(platform.system(), "Linux", "SETUP: Linux amd64 only")
        self.assertEqual(platform.machine(), "x86_64", "SETUP: Linux amd64 only")

        # Shared three-field schema, not a render of the Kubernetes chart. The chart's
        # responseMetadata expressions and native HTTP metadata use the same CEL.
        metadata = {
            field: f'json(response.headers["{HEADER}"]).{field}'
            for field in ("contract_version", "principal_id", "permissions")
        }
        context = {
            "contract_version": 1,
            "principal_id": "synthetic-principal",
            "permissions": ["", "llm:invoke"],
        }
        compact = lambda value: json.dumps(value, separators=(",", ":"))
        context["permissions"][0] = "p" * (65536 - len(compact(context)))
        auth_header = compact(context)
        self.assertEqual(len(auth_header.encode("ascii")), 64 * 1024)
        self.assertEqual(set(metadata), set(context))
        decision = (
            "type(extauthz.contract_version) == int && extauthz.contract_version == 1"
            ' && type(extauthz.principal_id) == string && extauthz.principal_id == "synthetic-principal"'
            " && type(extauthz.permissions) == list && size(extauthz.permissions) == 2"
            f" && size(extauthz.permissions[0]) == {len(context['permissions'][0])}"
            ' && extauthz.permissions[1] == "llm:invoke"'
        )
        for native_mcp in (False, True):
            for extraction, skip_auth in (
                [("header", False)] if native_mcp else
                [(mode, skip) for mode in ("body", "header") for skip in (False, True)]
            ):
                label = f"{extraction}, " + ("simulated auth skip (NOT verified JWT)" if skip_auth else "HTTP auth 64KiB")
                if native_mcp:
                    label += ", native stateless MCP (NO extProc)"
                with self.subTest(case=label):
                    sent = threading.Event()
                    release = threading.Event()
                    finished = threading.Event()
                    auth_calls = []
                    upstream_headers = []
                    upstream_methods = []
                    errors = []

                    class Handler(BaseHTTPRequestHandler):
                        protocol_version = "HTTP/1.1"

                        def setup(self):
                            super().setup()
                            self.connection.settimeout(TIMEOUT)

                        def log_message(self, *_):
                            pass

                        def do_DELETE(self):
                            if self.path == "/validate":
                                return self.do_GET()
                            errors.append("unexpected upstream DELETE in sessionless fixture")
                            self.send_error(405)

                        def do_POST(self):
                            if self.path == "/validate":
                                return self.do_GET()
                            try:
                                message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                                upstream_headers.append({k.lower(): v for k, v in self.headers.items()})
                                upstream_methods.append(message["method"])
                                if "id" not in message:
                                    self.send_response(202)
                                    self.send_header("Connection", "close")
                                    self.send_header("Content-Length", "0")
                                    self.end_headers()
                                    return
                                if message["method"] == "initialize":
                                    result = {
                                        "protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                                        "serverInfo": {"name": "synthetic", "version": "1"},
                                    }
                                elif message["method"] == "tools/list":
                                    result = {"tools": [{"name": "echo", "description": "Synthetic echo",
                                                         "inputSchema": {"type": "object"}}]}
                                elif message["method"] == "tools/call":
                                    if message["params"]["name"] != "echo":
                                        raise AssertionError("gateway did not remove target prefix")
                                    result = {"content": [{"type": "text", "text": "synthetic-result"}]}
                                else:
                                    raise AssertionError(f"unexpected MCP method: {message['method']}")
                                body = compact({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
                                self.send_response(200)
                                self.send_header("Connection", "close")
                                if message["method"] == "tools/call":
                                    self.send_header("Content-Type", "text/event-stream")
                                    self.end_headers()
                                    progress = {"jsonrpc": "2.0", "method": "notifications/progress",
                                                "params": {"progressToken": "synthetic-progress", "progress": 1}}
                                    self.wfile.write(b"event: message\ndata: " + compact(progress).encode() + b"\n\n")
                                    self.wfile.flush()
                                    sent.set()
                                    if not release.wait(3 * TIMEOUT):
                                        raise TimeoutError("MCP result hold was not released")
                                    self.wfile.write(b"event: message\ndata: " + body + b"\n\n")
                                    self.wfile.flush()
                                    finished.set()
                                else:
                                    self.send_header("Content-Type", "application/json")
                                    self.send_header("Content-Length", str(len(body)))
                                    self.end_headers()
                                    self.wfile.write(body)
                            except Exception as exc:
                                errors.append(repr(exc))
                            finally:
                                self.close_connection = True

                        def do_GET(self):
                            try:
                                if self.path == "/validate":
                                    auth_calls.append(self.request_version)
                                    # Header mode deliberately has no JSON decision in its body.
                                    body = auth_header.encode("ascii") if extraction == "body" else b"{}"
                                    self.send_response(200)
                                    self.send_header(HEADER, auth_header)
                                    self.send_header("Content-Length", str(len(body)))
                                    self.send_header("Connection", "close")
                                    self.end_headers()
                                    self.wfile.write(body)
                                    self.wfile.flush()
                                else:
                                    upstream_headers.append({k.lower(): v for k, v in self.headers.items()})
                                    self.send_response(200)
                                    self.send_header(HEADER, "upstream-sentinel")
                                    self.send_header("Content-Type", "text/event-stream")
                                    self.send_header("Connection", "close")
                                    self.end_headers()
                                    self.wfile.write(FIRST)
                                    self.wfile.flush()
                                    sent.set()
                                    if not release.wait(3 * TIMEOUT):
                                        raise TimeoutError("upstream hold was not released")
                                    self.wfile.write(LAST)
                                    self.wfile.flush()
                                    finished.set()
                            except Exception as exc:
                                errors.append(repr(exc))
                            finally:
                                self.close_connection = True

                    servers = []
                    threads = []
                    process = None
                    logs = ""
                    try:
                        for _ in range(2):
                            server = HTTPServer(("127.0.0.1", 0), Handler)
                            servers.append(server)
                            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
                            thread.start()
                            threads.append(thread)
                        auth_port, upstream_port = (s.server_port for s in servers)
                        with socket.socket() as reservation:
                            reservation.bind(("127.0.0.1", 0))
                            port = reservation.getsockname()[1]
                        expressions = metadata if extraction == "header" else {
                            field: f"json(response.body).{field}" for field in metadata
                        }
                        skip = 'request.path == "/simulated-skip"'
                        config = {
                            "config": {
                                "enableIpv6": False,
                                "adminAddr": "off",
                                "statsAddr": "off",
                                "readinessAddr": "off",
                                "workerThreads": "1",
                            },
                            "binds": [{
                                "port": port,
                                "listeners": [{
                                    "protocol": "HTTP",
                                    "routes": [{
                                        "backends": [{"host": f"127.0.0.1:{upstream_port}"}],
                                        "policies": {
                                            "extAuthz": {"conditional": [{
                                                "condition": f"!({skip})",
                                                "host": f"127.0.0.1:{auth_port}",
                                                "protocol": {"http": {
                                                    "path": '"/validate"',
                                                    "metadata": expressions,
                                                    "includeResponseHeaders": [],
                                                }},
                                            }]},
                                            "authorization": {"rules": [{"allow":
                                                f'source.address == "127.0.0.1" && (({skip}) || ({decision}))'
                                            }]},
                                            "transformations": {
                                                "request": {"remove": [HEADER]},
                                                "response": {"remove": [HEADER]},
                                            },
                                        },
                                    }],
                                }],
                            }],
                        }
                        if native_mcp:
                            config["binds"][0]["listeners"][0]["routes"][0]["backends"] = [{"mcp": {
                                "statefulMode": "stateless", "prefixMode": "always", "failureMode": "failClosed",
                                "targets": [{"name": "fixture", "mcp": {"host": f"http://127.0.0.1:{upstream_port}/mcp"}}],
                            }}]
                        # No inherited xDS, telemetry, proxy, or credential environment.
                        process = subprocess.Popen(
                            [str(binary), "-c", compact(config)],
                            env={"PATH": os.defpath, "RUST_LOG": "warn"},
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                        )
                        deadline = time.monotonic() + TIMEOUT
                        while True:
                            if process.poll() is not None:
                                logs = process.communicate(timeout=TIMEOUT)[0]
                                self.fail(f"gateway exited at startup:\n{logs}")
                            try:
                                client = socket.create_connection(("127.0.0.1", port), timeout=0.1)
                                break
                            except OSError:
                                self.assertLess(time.monotonic(), deadline, "gateway startup timed out")
                                time.sleep(0.05)
                        with client:
                            client.settimeout(TIMEOUT)
                            if native_mcp:
                                def request(method, message=None, session=None):
                                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
                                    headers = {"Accept": "application/json, text/event-stream",
                                               "Content-Type": "application/json", "MCP-Protocol-Version": "2025-11-25",
                                               HEADER: "caller-spoof", "Connection": "close"}
                                    if session is not None:
                                        headers["Mcp-Session-Id"] = session
                                    connection.request(method, "/mcp", compact(message) if message else None, headers)
                                    response = connection.getresponse()
                                    self.addCleanup(connection.close)
                                    self.assertIsNone(response.getheader("Mcp-Session-Id"))
                                    self.assertIsNone(response.getheader(HEADER))
                                    return response

                                def messages(body):
                                    if body.startswith(b"{"):
                                        return [json.loads(body)]
                                    return [json.loads(line[5:].strip()) for line in body.splitlines()
                                            if line.startswith(b"data:")]

                                initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                                    "protocolVersion": "2025-11-25", "capabilities": {},
                                    "clientInfo": {"name": "synthetic", "version": "1"},
                                }}
                                # Native stateless mode is NOT the extProc session-header rejection contract.
                                for session in (None, "caller-supplied-session"):
                                    response = request("POST", initialize, session)
                                    self.assertEqual(response.status, 200)
                                    self.assertEqual(messages(response.read())[-1]["result"]["protocolVersion"], "2025-11-25")
                                for method in ("GET", "DELETE"):
                                    before = len(upstream_methods)
                                    response = request(method)
                                    self.assertEqual(response.status, 405)
                                    response.read()
                                    self.assertEqual(len(upstream_methods), before)
                                response = request("POST", {"jsonrpc": "2.0", "method": "notifications/initialized"})
                                self.assertEqual(response.status, 202)
                                self.assertEqual(response.read(), b"")
                                response = request("POST", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                                self.assertEqual(response.status, 200)
                                tools = messages(response.read())[-1]["result"]["tools"]
                                self.assertEqual([tool["name"] for tool in tools], ["fixture_echo"])
                                response = request("POST", {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                    "params": {"name": tools[0]["name"], "arguments": {},
                                               "_meta": {"progressToken": "synthetic-progress"}}})
                                self.assertEqual(response.status, 200)
                                self.assertTrue(sent.wait(TIMEOUT))
                                self.assertIn("text/event-stream", response.getheader("Content-Type"))
                                first_message = []
                                while not first_message:
                                    line = response.readline()
                                    self.assertTrue(line, "MCP stream ended before progress notification")
                                    first_message = messages(line)
                                self.assertEqual(first_message[0]["method"], "notifications/progress")
                                self.assertFalse(finished.is_set(), "MCP result completed before progress arrived")
                                release.set()
                                result = messages(response.read())[-1]
                                self.assertEqual(result["id"], 3)
                                self.assertEqual(result["result"]["content"][0]["text"], "synthetic-result")
                                self.assertTrue(finished.wait(TIMEOUT))
                                self.assertEqual(auth_calls, ["HTTP/1.1"] * 7)
                                self.assertIn("tools/call", upstream_methods)
                                for headers in upstream_headers:
                                    self.assertNotIn(HEADER, headers)
                                    self.assertNotIn("mcp-session-id", headers)
                                self.assertEqual(errors, [])
                                print(f"PASS: {label}: initialize/list/call without session; GET/DELETE 405; "
                                      "POST progress before result/EOF. CAVEAT: incoming session header accepted "
                                      "without extProc; rejection must be validated independently.", flush=True)
                                continue
                            path = "/simulated-skip" if skip_auth else "/stream"
                            client.sendall(
                                f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                                f"{HEADER}: caller-spoof\r\nConnection: close\r\n\r\n".encode("ascii")
                            )
                            self.assertTrue(sent.wait(TIMEOUT), "upstream never flushed first SSE event")
                            self.assertEqual(auth_calls, [] if skip_auth else ["HTTP/1.1"])
                            self.assertNotIn(HEADER, upstream_headers[0])
                            if extraction == "body":
                                # Start the observation window only after the upstream flush.
                                readable, _, _ = select.select([client], [], [], 0.5)
                                self.assertFalse(readable, "body baseline unexpectedly delivered bytes before EOF")
                                self.assertFalse(finished.is_set())
                                release.set()
                            response = http.client.HTTPResponse(client)
                            try:
                                response.begin()
                                self.assertEqual(response.status, 200)
                                self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
                                self.assertIsNone(response.getheader(HEADER))
                                self.assertEqual(response.read(len(FIRST)), FIRST)
                                if extraction == "header":
                                    self.assertFalse(release.is_set())
                                    self.assertFalse(finished.is_set(), "upstream reached EOF before first event")
                                    release.set()
                                self.assertEqual(response.read(), LAST)
                            finally:
                                response.close()
                            self.assertTrue(finished.wait(TIMEOUT))
                            self.assertEqual(errors, [])
                            print(f"PASS: {label}: " + (
                                "no bytes before EOF; full SSE after release"
                                if extraction == "body" else "headers + first SSE before EOF; remainder after release"
                            ), flush=True)
                    finally:
                        release.set()
                        if process is not None:
                            if process.poll() is None:
                                process.terminate()
                            try:
                                logs = process.communicate(timeout=TIMEOUT)[0]
                            except subprocess.TimeoutExpired:
                                process.kill()
                                logs = process.communicate(timeout=TIMEOUT)[0]
                        for server in servers:
                            server.shutdown()
                            server.server_close()
                        for thread in threads:
                            thread.join(timeout=TIMEOUT)
                            self.assertFalse(thread.is_alive(), "loopback server did not stop")
                        if errors or not finished.is_set():
                            print(f"gateway diagnostics ({label}):\n{logs}\nfixture errors: {errors}", flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
