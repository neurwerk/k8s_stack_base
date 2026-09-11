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
        for extraction in ("body", "header"):
            for skip_auth in (False, True):
                label = f"{extraction}, " + ("simulated auth skip (NOT verified JWT)" if skip_auth else "HTTP auth 64KiB")
                with self.subTest(case=label):
                    sent = threading.Event()
                    release = threading.Event()
                    finished = threading.Event()
                    auth_calls = []
                    upstream_headers = []
                    errors = []

                    class Handler(BaseHTTPRequestHandler):
                        protocol_version = "HTTP/1.1"

                        def setup(self):
                            super().setup()
                            self.connection.settimeout(TIMEOUT)

                        def log_message(self, *_):
                            pass

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
                                            "transformations": {"request": {"remove": [HEADER]}},
                                        },
                                    }],
                                }],
                            }],
                        }
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
