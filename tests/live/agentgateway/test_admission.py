"""Opt-in billing admission regression against the pinned native gateway."""

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_accounting import port, post
from test_streaming import HEADER, SHA256
from helm import render, resource
from test_agentgateway_inspection import binding_values


class AdmissionTest(unittest.TestCase):
    def test_unsupported_provider_header_never_reaches_upstream(self):
        binary = Path(os.environ.get("AGENTGATEWAY_BIN", ""))
        self.assertTrue(binary.is_file() and os.access(binary, os.X_OK), "SETUP: supply AGENTGATEWAY_BIN")
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), SHA256)
        rendered = render("agentgateway", binding_values())
        context = {
            "contract_version": 1,
            "principal_id": "synthetic-principal",
            "credential_id": "synthetic-key",
            "credential_kind": "personal",
            "permissions": ["llm:invoke", "provider:openai:company-use", "model:example-chat:invoke", "model:local-chat:invoke"],
        }
        auth_calls, upstream_calls = [], []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length", "0")))
                self.do_GET()

            def do_GET(self):
                self.send_response(200)
                if self.path == "/validate":
                    auth_calls.append(self.path)
                    self.send_header(HEADER, json.dumps(context))
                else:
                    upstream_calls.append(self.path)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            for listener, path, host, personal in [
                ("api-bindings", "/bindings/openai/v1/responses", "provider.example.test", True),
                ("inspect-openai", "/v1/responses", "provider.example.test", True),
                ("api-bindings", "/bindings/local/v1/chat/completions", "local.example.test", False),
            ]:
                with self.subTest(listener=listener, path=path), tempfile.TemporaryDirectory(prefix="gateway-admission-") as temporary:
                    expression = resource(rendered, "AgentgatewayPolicy", f"ai-admission-{listener}")["spec"]["traffic"]["authorization"]["policy"]["matchExpressions"][0]
                    gateway = port()
                    config = {
                        "config": {"enableIpv6": False, "adminAddr": "off", "statsAddr": "off", "readinessAddr": "off", "workerThreads": "1"},
                        "gateways": {"api": {"port": gateway, "bindAddress": "127.0.0.1"}},
                        "routes": [{
                            "gateways": "api",
                            "backends": [{"host": f"127.0.0.1:{server.server_port}"}],
                            "policies": {
                                "extAuthz": {"host": f"127.0.0.1:{server.server_port}", "protocol": {"http": {
                                    "path": '"/validate"',
                                    "metadata": {field: f'json(response.headers["{HEADER}"]).{field}' for field in context},
                                    "includeResponseHeaders": [],
                                }}},
                                "authorization": {"rules": [{"allow": expression}]},
                            },
                        }],
                    }
                    log_path = Path(temporary) / "gateway.log"
                    with log_path.open("w") as log:
                        process = subprocess.Popen([str(binary.resolve()), "-c", json.dumps(config)], stdout=log, stderr=log,
                                                   env={"PATH": os.defpath, "HOME": temporary})
                        try:
                            for _ in range(100):
                                self.assertIsNone(process.poll(), log_path.read_text())
                                try:
                                    with socket.create_connection(("127.0.0.1", gateway), timeout=0.1):
                                        break
                                except OSError:
                                    time.sleep(0.05)
                            else:
                                self.fail("gateway not ready: " + log_path.read_text())
                            cases = [("company or local", {}, 200),
                                     ("unsupported", {"api-key": "synthetic-provider-key"}, 403),
                                     ("empty unsupported", {"api-key": ""}, 403)]
                            if personal:
                                cases.append(("personal", {"authorization": "Bearer synthetic-provider-key"}, 200))
                            for label, headers, expected in cases:
                                with self.subTest(case=label):
                                    upstream_calls.clear()
                                    auth_calls.clear()
                                    status, _ = post(gateway, path, {"model": "example-chat"}, {
                                        "host": host, "x-neurwerk-authorization": "Bearer synthetic-platform-key", **headers,
                                    })
                                    self.assertEqual(auth_calls, ["/validate"])
                                    self.assertEqual(status, expected)
                                    self.assertEqual(upstream_calls, [path] if expected == 200 else [])
                        finally:
                            process.terminate()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
