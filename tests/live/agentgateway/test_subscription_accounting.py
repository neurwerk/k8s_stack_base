"""Pinned-native Responses cost isolation; run with opentelemetry-proto==1.38.0."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_accounting import SHA256, binding_values, port, post, render, resource
from celpy import Environment, celtypes, json_to_cel
from helm import documents


class SubscriptionAccountingTest(unittest.TestCase):
    def test_included_responses_cost_isolated_from_metered_openai(self):
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

        binary = Path(os.environ.get("AGENTGATEWAY_BIN", ""))
        self.assertTrue(binary.is_file() and os.access(binary, os.X_OK), "SETUP: supply AGENTGATEWAY_BIN")
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), SHA256)
        values = binding_values()
        values["authKeycloak"]["agentgatewayClientRoles"].append("model:example-codex:invoke")
        values["aiGateway"]["bindings"].append({
            "id": "codex", "enabled": True, "providerId": "openai", "provider": "OpenAI",
            "upstreamURL": "https://chatgpt.com/backend-api/codex", "credentialSources": ["caller_supplied"],
            "inspect": True, "profiles": ["openai-codex-responses-v1"], "billingMode": "included",
            "models": [{"name": "example-codex", "upstreamModel": "shared"}],
        })
        rendered = render("agentgateway", values)
        raw = resource(rendered, "AgentgatewayParameters")["spec"]["rawConfig"]
        included_model = next(doc["spec"] for doc in documents(rendered) if doc["kind"] == "AgentgatewayModel"
                              and doc["metadata"]["name"].startswith("ai-codex-") and doc["spec"]["visibility"] == "Internal")
        self.assertEqual(included_model["provider"], "Custom")
        custom = copy.deepcopy(included_model["custom"])
        # Kubernetes API enums use title case; standalone native config uses lower case.
        for format in custom["formats"]:
            format["type"] = format["type"].lower()
        expression = raw["frontendPolicies"]["accessLog"]["add"]["neurwerk.billing_classification"]
        environment = Environment()
        # cel-python lacks native CEL's dynamic map/null inequality overload.
        program = environment.program(environment.compile(expression), functions={
            "_!=_": lambda left, right: celtypes.BoolType((left is not right) if left is None or right is None else left != right),
        })
        for source, dispatched, expected in [("caller_supplied", "codex", "subscription"),
                                              ("caller_supplied", "openai", "unknown"),
                                              ("company", "codex", "unknown")]:
            context = {"metadata": {"neurwerk_admission": {"provider_credential_source": source}},
                       "extproc": {"neurwerk.request_context": {"dispatch_binding_id": dispatched}}}
            self.assertEqual(program.evaluate({key: json_to_cel(value) for key, value in context.items()}), expected)
        for context in [{"metadata": {}, "extproc": None},
                        {"metadata": {"neurwerk_admission": {"provider_credential_source": "caller_supplied"}}, "extproc": None}]:
            self.assertEqual(program.evaluate({key: json_to_cel(value) for key, value in context.items()}), "unknown")
        # OAuth transport alone must not select the zero-rate provider namespace.
        unconfirmed_values = copy.deepcopy(values)
        unconfirmed_values["aiGateway"]["bindings"][-1].pop("billingMode")
        unconfirmed = render("agentgateway", unconfirmed_values)
        unconfirmed_model = next(doc["spec"] for doc in documents(unconfirmed) if doc["kind"] == "AgentgatewayModel"
                                and doc["metadata"]["name"].startswith("ai-codex-") and doc["spec"]["visibility"] == "Internal")
        self.assertEqual(unconfirmed_model["provider"], "OpenAI")
        self.assertNotIn("custom", unconfirmed_model)
        calls, spans = [], []

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["content-length"])))
                case = self.headers["x-fixture-case"]
                calls.append((case, self.path, request["model"]))
                data = {"id": "resp_fixture", "object": "response", "created_at": 1,
                        "status": "completed", "model": request["model"], "output": [],
                        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}}
                if case == "missing":
                    data.pop("usage")
                if case == "zero":
                    data["usage"] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                encoded = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

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
        process = None
        try:
            with tempfile.TemporaryDirectory(prefix="gateway-subscription-") as temporary:
                gateway, admin = port(), port()
                admission = {field: "fixture" for field in ["request_id", "credential_id", "credential_kind", "policy_revision", "binding_id"]}
                admission.update(entry_mode="api", destination_kind="model", provider_id="openai",
                                 protocol_profile="openai-codex-responses-v1", operation_id="create", provider_credential_source="caller_supplied")
                access = copy.deepcopy(raw["frontendPolicies"]["accessLog"])
                for fields in (access["add"], access["database"]["add"]):
                    fields["fixture.case"] = 'request.headers["x-fixture-case"]'
                trace_fields = copy.deepcopy(raw["config"]["tracing"]["fields"])
                trace_fields["add"]["fixture.case"] = 'request.headers["x-fixture-case"]'
                config = {
                    "config": {"adminAddr": f"127.0.0.1:{admin}", "statsAddr": "off", "readinessAddr": "off",
                               "logging": {"level": "info", "format": "json", "database": {"url": f"sqlite://{temporary}/requests.sqlite"}},
                               "tracing": {"otlpEndpoint": f"http://127.0.0.1:{collector.server_port}/v1/traces", "otlpProtocol": "http",
                                           "randomSampling": True, "fields": trace_fields},
                               "modelCatalog": [{"inline": {"providers": {
                                   "codex": {"models": {"shared": {"rates": {"input": "0", "output": "0"}}}},
                                   "openai": {"models": {"shared": {"rates": {"input": "1", "output": "2"}}}},
                               }}}]},
                    "frontendPolicies": {"accessLog": access},
                    "gateways": {"api": {"port": gateway, "bindAddress": "127.0.0.1", "transformations": {
                        "request": {"metadata": {"neurwerk_admission": json.dumps(admission)}}}}},
                    "llm": {"gateways": "api", "models": [
                        {"name": name, "provider": {"custom": custom},
                         "params": {"model": "unpriced" if name == "unpriced" else "shared",
                                    "baseUrl": included_model["baseURL"].replace("https://chatgpt.com", f"http://127.0.0.1:{upstream.server_port}")}}
                        for name in ["included", "zero", "missing", "unpriced"]
                    ] + [{"name": "metered", "provider": "openAI",
                          "params": {"model": "shared", "baseUrl": f"http://127.0.0.1:{upstream.server_port}/v1"}}]},
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
                    cases = ["included", "metered", "zero", "missing", "unpriced"]
                    for case in cases:
                        status, payload = post(gateway, "/v1/responses", {"model": case, "input": "safe"}, {"x-fixture-case": case})
                        self.assertEqual(status, 200, payload)
                    for _ in range(200):
                        status, payload = post(admin, "/api/logs/search", {"limit": 100, "includeAttributes": True})
                        self.assertEqual(status, 200)
                        rows = {row["attributes"]["fixture.case"]: row for row in json.loads(payload)["logs"]}
                        traced = {}
                        for span in list(spans):
                            fields = {item.key: getattr(item.value, item.value.WhichOneof("value")) for item in span.attributes}
                            if "fixture.case" in fields:
                                traced[fields["fixture.case"]] = fields
                        if set(rows) == set(cases) and set(traced) == set(cases):
                            break
                        time.sleep(0.05)
                    self.assertEqual(set(rows), set(cases), log_path.read_text())
                    self.assertEqual(set(traced), set(cases), log_path.read_text())
                    self.assertEqual(calls, [(case, "/v1/responses" if case == "metered" else "/backend-api/codex/responses",
                                             "unpriced" if case == "unpriced" else "shared") for case in cases])
                    for case in cases:
                        expected_known = case not in ["missing", "unpriced"]
                        self.assertIs(rows[case]["attributes"]["neurwerk.cost_known"], expected_known, case)
                        self.assertIs(rows[case]["attributes"]["neurwerk.token_usage_known"], case != "missing", case)
                        # Native trace custom attributes are strings; the collector normalizes flags.
                        self.assertEqual(traced[case]["neurwerk.cost_known"], str(expected_known).lower(), case)
                        self.assertEqual(traced[case]["neurwerk.token_usage_known"], str(case != "missing").lower(), case)
                        if case != "missing":
                            self.assertEqual(rows[case]["usage"]["totalTokens"], 0 if case == "zero" else 5)
                        if expected_known:
                            expected_cost = 0.000007 if case == "metered" else 0
                            self.assertAlmostEqual(rows[case]["cost"], expected_cost)
                            self.assertAlmostEqual(float(traced[case]["gen_ai.usage.cost"]), expected_cost)
                    self.assertIsNone(rows["unpriced"]["cost"])
                    self.assertNotIn("gen_ai.usage.cost", traced["unpriced"])
        finally:
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
