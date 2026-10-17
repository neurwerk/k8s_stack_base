"""Opt-in native accounting regression using the chart's actual CEL expressions."""

import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "charts"))
from helm import render, resource
from test_agentgateway_inspection import binding_values
from test_streaming import SHA256


def port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def post(port, path, body, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("POST", path, json.dumps(body), {"content-type": "application/json", **(headers or {})})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class AccountingTest(unittest.TestCase):
    def test_known_native_reported_accounting(self):
        binary = Path(os.environ.get("AGENTGATEWAY_BIN", ""))
        self.assertTrue(binary.is_file() and os.access(binary, os.X_OK), "SETUP: supply AGENTGATEWAY_BIN; see README.md")
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), SHA256)
        raw = resource(render("agentgateway", binding_values()), "AgentgatewayParameters")["spec"]["rawConfig"]

        for provider, profile in [("openai", "openai-chat-completions-v1"), ("anthropic", "anthropic-messages-v1")]:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory(prefix="gateway-accounting-") as temporary:
                calls = []

                class Provider(BaseHTTPRequestHandler):
                    def log_message(self, *_):
                        pass

                    def do_POST(self):
                        request = json.loads(self.rfile.read(int(self.headers["content-length"])))
                        case = self.headers["x-fixture-case"]
                        calls.append(case)
                        counts = {"input_tokens": 0, "output_tokens": 0} if case == "zero" else {"input_tokens": 3, "output_tokens": 2}
                        if case == "partial":
                            counts.pop("output_tokens")
                        if provider == "anthropic":
                            data = {"id": "msg_fixture", "type": "message", "role": "assistant", "model": request["model"],
                                    "content": [{"type": "text", "text": "safe"}], "stop_reason": "end_turn", "usage": counts}
                        else:
                            data = {"id": "fixture", "object": "chat.completion", "created": 1, "model": request["model"],
                                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "safe"}, "finish_reason": "stop"}],
                                    "usage": {{"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens"}[k]: v for k, v in counts.items()}}
                        if case == "missing":
                            data.pop("usage")
                        status = 401 if case == "provider-error" else 200
                        if status == 401:
                            data = {"error": {"type": "authentication_error", "message": "synthetic failure"}}
                        encoded = json.dumps(data).encode()
                        self.send_response(status)
                        self.send_header("content-type", "application/json")
                        self.send_header("content-length", str(len(encoded) + (100 if case == "interrupted" else 0)))
                        self.end_headers()
                        self.wfile.write(encoded)
                        self.wfile.flush()
                        self.close_connection = True

                upstream = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
                thread = threading.Thread(target=upstream.serve_forever, daemon=True)
                thread.start()
                process = None
                try:
                    gateway, admin = port(), port()
                    admission = {field: "fixture" for field in ["request_id", "credential_id", "credential_kind", "policy_revision", "binding_id"]}
                    admission.update(entry_mode="api", destination_kind="model", provider_id=provider,
                                     protocol_profile=profile, operation_id="create", provider_credential_source="caller_supplied")
                    access = copy.deepcopy(raw["frontendPolicies"]["accessLog"])
                    for fields in (access["add"], access["database"]["add"]):
                        fields["fixture.case"] = 'request.headers["x-fixture-case"]'
                    config = {
                        "config": {"adminAddr": f"127.0.0.1:{admin}", "statsAddr": "off", "readinessAddr": "off",
                                   "logging": {"level": "info", "format": "json", "database": {"url": f"sqlite://{temporary}/requests.sqlite"}},
                                   "modelCatalog": [{"inline": {"providers": {provider: {"models": {
                                       "priced": {"rates": {"input": "1", "output": "2"}},
                                       "free": {"rates": {"input": "0", "output": "0"}},
                                   }}}}}]},
                        "frontendPolicies": {"accessLog": access},
                        "gateways": {"api": {"port": gateway, "bindAddress": "127.0.0.1", "transformations": {"conditional": [{
                            "condition": 'request.headers["x-fixture-case"] != "historical"',
                            "request": {"metadata": {"neurwerk_admission": json.dumps(admission)}},
                        }]}}},
                        "llm": {"gateways": "api", "models": [{"name": name, "provider": "openAI" if provider == "openai" else provider,
                            "params": {"baseUrl": f"http://127.0.0.1:{upstream.server_port}/v1"}} for name in ["priced", "free", "unpriced"]]},
                    }
                    log_path = Path(temporary) / "gateway.log"
                    with log_path.open("w") as log:
                        process = subprocess.Popen([str(binary.resolve()), "-c", json.dumps(config)], stdout=log, stderr=log,
                                                   env={"PATH": os.defpath, "HOME": temporary})
                        for _ in range(100):
                            try:
                                if post(admin, "/api/logs/search", {"limit": 1})[0] == 200:
                                    break
                            except OSError:
                                pass
                            self.assertIsNone(process.poll(), log_path.read_text())
                            time.sleep(0.05)
                        else:
                            self.fail("gateway not ready: " + log_path.read_text())
                        cases = ["full", "zero", "partial", "missing", "provider-error", "interrupted", "free", "unpriced", "historical"]
                        statuses = {}
                        for case in cases:
                            body = {"model": case if case in ["free", "unpriced"] else "priced",
                                    "messages": [{"role": "user", "content": "safe"}], "max_tokens": 16}
                            path = "/v1/messages" if provider == "anthropic" else "/v1/chat/completions"
                            statuses[case] = post(gateway, path, body, {"x-fixture-case": case})[0]
                        for _ in range(100):
                            status, payload = post(admin, "/api/logs/search", {"limit": 100, "includeAttributes": True})
                            self.assertEqual(status, 200)
                            rows = {row["attributes"]["fixture.case"]: row for row in json.loads(payload)["logs"]}
                            if len(rows) == len(cases):
                                break
                            time.sleep(0.05)
                        self.assertEqual(set(rows), set(cases))
                        self.assertEqual(calls, cases)
                        self.assertEqual(statuses["provider-error"], 401)
                        self.assertEqual(statuses["interrupted"], 502)
                        stdout = {row["fixture.case"]: row for line in log_path.read_text().splitlines()
                                  if line.startswith("{") and "\"fixture.case\"" in line for row in [json.loads(line)]}
                        for case, row in rows.items():
                            usage_known = all(row["usage"][field] is not None for field in ["inputTokens", "outputTokens", "totalTokens"])
                            for field in ["token_usage_known", "cost_known"]:
                                key = f"neurwerk.{field}"
                                expected = usage_known and (field == "token_usage_known" or case != "unpriced")
                                self.assertIs(row["attributes"].get(key), None if case == "historical" else expected, case)
                                self.assertEqual(stdout[case].get(key), row["attributes"].get(key))
                            if case != "historical":
                                self.assertEqual(row["attributes"]["neurwerk.billing_classification"], "unknown")
                                self.assertEqual(row["attributes"]["neurwerk.request_text_inspection"], "unknown")
                                self.assertEqual(row["attributes"]["neurwerk.target_credential_mode"], "unknown")
                                self.assertIs(row["attributes"]["neurwerk.dispatch_attempted"], True)
                                self.assertEqual(row["attributes"]["neurwerk.request_outcome"],
                                                 "unknown" if statuses[case] == 200 else
                                                 "http_error" if case == "provider-error" else "gateway_error", case)
                        # Known means native reported accounting, not raw completeness or delivery.
                        for case in ["full", "zero", "free"]:
                            self.assertIs(rows[case]["attributes"]["neurwerk.token_usage_known"], True)
                            self.assertIs(rows[case]["attributes"]["neurwerk.cost_known"], True)
                        for case in ["missing", "provider-error", "interrupted"]:
                            self.assertIs(rows[case]["attributes"]["neurwerk.token_usage_known"], False)
                            self.assertIs(rows[case]["attributes"]["neurwerk.cost_known"], False)
                        self.assertIs(rows["unpriced"]["attributes"]["neurwerk.token_usage_known"], True)
                        self.assertIs(rows["unpriced"]["attributes"]["neurwerk.cost_known"], False)
                        self.assertEqual(rows["full"]["usage"]["totalTokens"], 5)
                        self.assertAlmostEqual(rows["full"]["cost"], 0.000007)
                        self.assertEqual(rows["zero"]["usage"]["totalTokens"], 0)
                        self.assertEqual(rows["zero"]["cost"], 0)
                        self.assertEqual(rows["free"]["usage"]["totalTokens"], 5)
                        self.assertEqual(rows["free"]["cost"], 0)
                        self.assertIsNone(rows["unpriced"]["cost"])
                        self.assertIsNone(rows["provider-error"]["usage"]["totalTokens"])
                        if provider == "openai":
                            self.assertEqual(rows["partial"]["usage"], {"inputTokens": 3, "outputTokens": 0, "totalTokens": 3})
                            self.assertIsNone(rows["missing"]["usage"]["totalTokens"])
                            self.assertEqual(rows["missing"]["cost"], 0)
                finally:
                    if process is not None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                    upstream.shutdown()
                    upstream.server_close()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
