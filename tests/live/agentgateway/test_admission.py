"""Opt-in raw-wire admission and full transformation chain on pinned native 1.6.0.

Synthetic Bridge and upstream only: no JWT verification, extProc or provider API.
"""

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_accounting import port
from test_streaming import HEADER, SHA256
from helm import render, resource
from test_agentgateway_inspection import binding_values


def transformations(value):
    """Translate chart action lists/conditional envelopes to the native local API."""
    if "conditional" in value:
        return {"conditional": [{"condition": branch["condition"], **transformations(branch["policy"])}
                                for branch in value["conditional"]]}
    result = copy.deepcopy(value)
    for direction in result.values():
        for action in ["set", "add"]:
            if action in direction:
                direction[action] = {item["name"]: item["value"] for item in direction[action]}
    return result


class AdmissionTest(unittest.TestCase):
    def test_raw_modes_and_full_native_transformation_chain(self):
        binary = Path(os.environ.get("AGENTGATEWAY_BIN", ""))
        self.assertTrue(binary.is_file() and os.access(binary, os.X_OK), "SETUP: supply AGENTGATEWAY_BIN")
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), SHA256)
        values = binding_values()
        values["mcp-servers"] = {"enabled": True, "contextforgeRoutesEnabled": ["context7"],
                                 "catalog": {"presets": {"context7": {"enabled": True}}}}
        values["authKeycloak"]["agentgatewayClientRoles"].append("mcp:context7:invoke")
        rendered = render("agentgateway", values)
        identity = {
            "contract_version": 1, "principal_id": "synthetic-principal",
            "credential_id": "synthetic-key", "credential_kind": "personal", "account_email": None,
            "permissions": values["authKeycloak"]["agentgatewayClientRoles"],
        }
        upstream_calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length", "0")))
                self.do_GET()

            def do_GET(self):
                if self.path in ["/validate/neurwerk", "/validate/proxy"]:
                    valid = (self.headers.get("x-neurwerk-authorization") == "Bearer synthetic-platform"
                             if self.path == "/validate/neurwerk" else
                             self.headers.get("proxy-authorization") == "Basic c3ludGhldGljOnN5bnRoZXRpYw==")
                    self.send_response(200 if valid else 403)
                    self.send_header(HEADER, json.dumps(identity))
                    self.send_header("x-request-id", "synthetic-request")
                else:
                    upstream_calls.append((self.path, dict(self.headers)))
                    self.send_response(200)
                    # Verify the rendered response sanitation too.
                    self.send_header("x-neurwerk-credential-mode", "upstream-sentinel")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            pre = resource(rendered, "AgentgatewayPolicy", "ai-admission-api-bindings")["spec"]["traffic"]
            auth = pre["extAuth"]["conditional"][0]["policy"]["http"]
            gateways, routes, ports = {}, [], {}
            for name, route_name in [
                ("openai", "ai-openai-api-processing"), ("local", "ai-local-api-processing"),
                ("mcp", "mcp-context7-policy"), ("discovery", "ai-openai-model-discovery-processing"),
                ("inspected", "ai-openai-proxy-processing"),
            ]:
                selected_pre = (resource(rendered, "AgentgatewayPolicy", "ai-admission-inspect-openai")["spec"]["traffic"]
                                if name == "inspected" else pre)
                auth = selected_pre["extAuth"]["conditional"][0]["policy"]["http"]
                gateway = ports[name] = port()
                gateways[name] = {
                    "port": gateway, "bindAddress": "127.0.0.1",
                    "extAuthz": {"host": f"127.0.0.1:{server.server_port}",
                                 "includeRequestHeaders": auth["allowedRequestHeaders"], "protocol": {"http": {
                                     "path": auth["path"], "addRequestHeaders": auth["addRequestHeaders"],
                                     "metadata": auth["responseMetadata"], "includeResponseHeaders": [],
                                 }}},
                    "authorization": {"rules": [{"allow": selected_pre["authorization"]["policy"]["matchExpressions"][0]}]},
                    "transformations": transformations(selected_pre["transformation"]),
                }
                traffic = resource(rendered, "AgentgatewayPolicy", route_name)["spec"]["traffic"]
                policies = {"authorization": {"rules": [{
                    "allow": traffic["authorization"]["policy"]["matchExpressions"][0],
                }]}}
                if "transformation" in traffic:
                    policies["transformations"] = transformations(traffic["transformation"])
                # Observe trusted metadata after gateway stripping and route admission.
                backend_policies = {"transformations": {"request": {"set": {
                    "x-test-frozen-source": 'has(metadata.neurwerk_admission) ? metadata.neurwerk_admission.provider_credential_source : metadata.neurwerk_identity.provider_credential_source',
                }}}}
                routes.append({"gateways": name, "policies": policies, "backends": [{
                    "host": f"127.0.0.1:{server.server_port}", "policies": backend_policies,
                }]})
            inspected_gateway = gateways.pop("inspected")
            inspected_gateway.pop("port")
            inspected_gateway.pop("bindAddress")
            inspected_route = routes.pop()
            inspected_route.pop("gateways")
            config = {
                "config": {"enableIpv6": False, "adminAddr": "off", "statsAddr": "off",
                           "readinessAddr": "off", "workerThreads": "1"},
                "gateways": gateways, "routes": routes,
                "binds": [
                    {"port": port(), "protocol": "HTTP", "tunnelProtocol": "connect", "listeners": []},
                    {"port": ports["inspected"], "mode": "internal", "protocol": "HTTP", "listeners": [{
                        "hostname": "provider.example.test", "protocol": "HTTP", "policies": inspected_gateway,
                        "routes": [inspected_route],
                    }]},
                ],
            }
            with tempfile.TemporaryDirectory(prefix="gateway-admission-", dir=os.environ.get("TMPDIR", "/tmp/opencode")) as temporary:
                log_path = Path(temporary) / "gateway.log"
                with log_path.open("w") as log:
                    process = subprocess.Popen([str(binary.resolve()), "-c", json.dumps(config)], stdout=log, stderr=log,
                                               env={"PATH": os.defpath, "HOME": temporary})
                    try:
                        for _ in range(100):
                            self.assertIsNone(process.poll(), log_path.read_text())
                            try:
                                with socket.create_connection(("127.0.0.1", ports["openai"]), timeout=0.1):
                                    break
                            except OSError:
                                time.sleep(0.05)
                        else:
                            self.fail("gateway not ready: " + log_path.read_text())

                        selector = "X-Neurwerk-Credential-Mode"
                        bearer = ("Authorization", "Bearer incidental")
                        cases = [
                            ("openai", [], 200, "global"),
                            ("openai", [bearer], 200, "override"),
                            ("openai", [(selector, "global"), bearer], 200, "global"),
                            ("local", [(selector, " \tnone\t "), bearer], 200, "none"),
                            ("mcp", [(selector, "none"), bearer], 200, "none"),
                            ("mcp", [(selector, "global")], 403, None),
                            ("openai", [(selector, "override"), bearer], 200, "override"),
                            ("openai", [(selector, "override"), bearer, ("Cookie", "synthetic=value")], 403, None),
                            ("openai", [(selector, "override")], 403, None),
                            ("openai", [(selector, "none")], 403, None),
                            ("openai", [(selector, "global"), ("x-api-key", "synthetic")], 403, None),
                            ("openai", [(selector, "global"), ("Authorization", "Basic incidental")], 403, None),
                            ("openai", [(selector, "global"), bearer, bearer], 403, None),
                            ("openai", [(selector, "global"), (selector.lower(), "global")], 403, None),
                            ("openai", [(selector, "global,global")], 403, None),
                            ("openai", [(selector, "Global")], 403, None),
                            ("openai", [(selector, "")], 403, None),
                            ("discovery", [(selector, " \toverride ")], 200, "override"),
                            ("discovery", [(selector, "global"), bearer], 200, "global"),
                            ("discovery", [bearer], 403, None),
                        ]
                        cases = [(*case, {}) for case in cases] + [
                            ("inspected", [], 200, "global", {}),
                            ("inspected", [bearer], 200, "override", {}),
                            ("inspected", [(selector, "global"), bearer], 403, None, {}),
                            ("inspected", [bearer], 403, None, {selector: "global"}),
                            ("inspected", [bearer], 403, None, {selector: ""}),
                        ]
                        for name, lines, expected, source, connect_headers in cases:
                            with self.subTest(name=name, lines=lines):
                                upstream_calls.clear()
                                path = ("/v1/chat/completions" if name == "inspected" else
                                        "/mcp/context7" if name == "mcp" else
                                        "/bindings/openai/v1/models" if name == "discovery" else
                                        f"/bindings/{name}/v1/chat/completions")
                                conn = http.client.HTTPConnection("127.0.0.1", config["binds"][0]["port"]
                                                                  if name == "inspected" else ports[name], timeout=5)
                                try:
                                    if name == "inspected":
                                        conn.set_tunnel("provider.example.test", ports[name], headers={
                                            "Proxy-Authorization": "Basic c3ludGhldGljOnN5bnRoZXRpYw==", **connect_headers,
                                        })
                                        # CONNECT may succeed; rejection belongs to the tunneled request.
                                        conn.connect()
                                    conn.putrequest("GET" if name == "discovery" else "POST", path, skip_host=True)
                                    conn.putheader("Host", "provider.example.test")
                                    conn.putheader("x-neurwerk-authorization", "Bearer synthetic-platform")
                                    conn.putheader("x-agentgateway-auth-context", '{"provider_credential_source":"spoof"}')
                                    for header, value in lines:
                                        # Separate putheader calls produce actual repeated wire lines.
                                        conn.putheader(header, value)
                                    conn.putheader("Content-Length", "2")
                                    conn.endheaders(b"{}")
                                    response = conn.getresponse()
                                    response.read()
                                    self.assertEqual(response.status, expected, log_path.read_text())
                                    self.assertIsNone(response.getheader(selector))
                                finally:
                                    conn.close()
                                self.assertEqual(len(upstream_calls), int(expected == 200))
                                if expected == 200:
                                    sent = {key.lower(): value for key, value in upstream_calls[0][1].items()}
                                    self.assertEqual(sent["x-test-frozen-source"], source)
                                    self.assertNotIn("x-agentgateway-auth-context", sent)
                                    self.assertNotIn("proxy-authorization", sent)
                                    if name == "discovery":
                                        self.assertEqual(sent[selector.lower()], source)
                                        self.assertIn("x-neurwerk-authorization", sent)
                                        self.assertNotIn("authorization", sent)
                                    else:
                                        self.assertNotIn(selector.lower(), sent)
                                        self.assertNotIn("x-neurwerk-authorization", sent)
                                        self.assertEqual("authorization" in sent, source == "override")
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
