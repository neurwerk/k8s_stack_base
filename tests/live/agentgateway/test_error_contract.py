"""Opt-in native correlation regression with the installed extProc under review.

Run with AGENTGATEWAY_BIN and the extProc checkout's `uv run --frozen --extra dev
python <base>/tests/live/agentgateway/test_error_contract.py` environment. All
servers are disposable loopback fixtures; the bridge is a synthetic HTTP peer.
"""

import asyncio
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import grpc.aio
import httpx

from agentgateway_extproc.config.settings import EngineSettings, Settings
from agentgateway_extproc.controllers.grpc_servicer import ExtProcServicer
from agentgateway_extproc.gen import ext_proc_pb2_grpc
from agentgateway_extproc.lib.engine.client import EngineClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "charts"))
from helm import render, resource
from test_agentgateway_inspection import binding_values
from test_accounting import port
from test_streaming import SHA256, TIMEOUT


PATH = "/bindings/openai/v1/chat/completions"
BODY = {"model": "example-chat", "stream": True,
        "messages": [{"role": "user", "content": "synthetic request"}]}


def frame(delta, finish=None):
    payload = {"id": "fixture", "object": "chat.completion.chunk", "created": 1,
               "model": "provider-chat", "choices": [{"index": 0, "delta": delta,
                                                       "finish_reason": finish}]}
    return b"data: " + json.dumps(payload, separators=(",", ":")).encode() + b"\n\n"


def configuration(rendered, gateway_port, auth_port, provider_port, processor_port):
    admission = resource(rendered, "AgentgatewayPolicy", "ai-admission-api-bindings")["spec"]["traffic"]
    auth = admission["extAuth"]["conditional"][0]
    http = auth["policy"]["http"]
    processing = resource(rendered, "AgentgatewayPolicy", "ai-openai-api-processing")["spec"]["traffic"]["extProc"]
    # Translate Kubernetes attachment/transport fields; execute the rendered CEL verbatim.
    return {
        "config": {"adminAddr": "off", "statsAddr": "off", "readinessAddr": "off", "workerThreads": "1"},
        "gateways": {"contract": {
            "port": gateway_port, "bindAddress": "127.0.0.1",
            "extAuthz": {"conditional": [{
                "condition": auth["condition"], "host": f"127.0.0.1:{auth_port}",
                "includeRequestHeaders": http["allowedRequestHeaders"],
                "protocol": {"http": {"path": http["path"],
                                      "addRequestHeaders": http["addRequestHeaders"],
                                      "metadata": http["responseMetadata"]}},
            }]},
            "authorization": {"rules": [{"allow": expression}
                                         for expression in admission["authorization"]["policy"]["matchExpressions"]]},
            "transformations": {"conditional": [
                {"condition": branch["condition"], **branch["policy"]}
                for branch in admission["transformation"]["conditional"]
            ]},
        }},
        "routes": [{
            "gateways": ["contract"], "matches": [{"path": {"exact": PATH}}],
            "backends": [{"host": f"127.0.0.1:{provider_port}"}],
            "policies": {"extProc": {
                "host": f"127.0.0.1:{processor_port}",
                "failureMode": "failClosed",
                "metadataContext": processing["metadataContext"],
                "processingOptions": {
                    key: value[0].lower() + value[1:] if isinstance(value, str) else value
                    for key, value in processing["processingOptions"].items()
                },
            }},
        }],
    }


class ErrorContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_rendered_ids_survive_auth_processing_and_streaming(self):
        binary = Path(os.environ.get("AGENTGATEWAY_BIN", ""))
        self.assertTrue(binary.is_file() and os.access(binary, os.X_OK), "SETUP: supply AGENTGATEWAY_BIN")
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), SHA256)
        self.assertEqual((platform.system(), platform.machine()), ("Linux", "x86_64"))
        values = binding_values()
        values["aiGateway"]["bindings"][0]["models"][0]["piiEnabled"] = False
        rendered = render("agentgateway", values)
        auth_calls, provider_calls, errors = [], [], []
        release, finished = threading.Event(), threading.Event()

        class Bridge(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                request_id = self.headers["x-request-id"]
                credential = self.headers.get("x-neurwerk-authorization")
                auth_calls.append((request_id, credential))
                if credential == "Bearer denied":
                    status = 401
                    body = {"detail": "Invalid or expired key", "error": {
                        "message": "Invalid or expired key", "code": "authentication_failed",
                        "request_id": request_id}}
                else:
                    status, body = 200, {}
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.send_header("x-request-id", request_id)
                if status == 200:
                    self.send_header("x-agentgateway-auth-context", json.dumps({
                        "contract_version": 1, "principal_id": "synthetic-principal",
                        "credential_id": "synthetic-credential", "credential_kind": "personal",
                        "account_email": None,
                        "permissions": [] if credential == "Bearer forbidden" else [
                            "llm:invoke", "model:example-chat:invoke", "provider:openai:company-use"],
                    }))
                self.end_headers()
                self.wfile.write(payload)

        class Provider(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    self.rfile.read(int(self.headers["content-length"]))
                    provider_calls.append(dict(self.headers.items()))
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.send_header("x-request-id", "provider-spoof")
                    self.send_header("connection", "close")
                    self.end_headers()
                    self.wfile.write(frame({"role": "assistant", "content": "first"}))
                    self.wfile.flush()
                    if not release.wait(3 * TIMEOUT):
                        raise TimeoutError("SSE EOF gate was never released")
                    self.wfile.write(frame({}, "stop") + b"data: [DONE]\n\n")
                    self.wfile.flush()
                    finished.set()
                except Exception as exc:
                    errors.append(repr(exc))
                finally:
                    self.close_connection = True

        def unexpected_engine_call(request):
            raise AssertionError("PII-disabled fixture unexpectedly called the Engine")

        engine = EngineClient(EngineSettings(base_url="http://127.0.0.1"),
                              httpx.AsyncClient(transport=httpx.MockTransport(unexpected_engine_call)))
        settings = Settings(grpc_maximum_concurrent_rpcs=1)
        processor = ExtProcServicer(engine, settings)
        server = grpc.aio.server(maximum_concurrent_rpcs=2)
        ext_proc_pb2_grpc.add_ExternalProcessorServicer_to_server(processor, server)
        processor_port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        peers, threads, connections = [], [], []
        process = None
        with tempfile.TemporaryDirectory(prefix="gateway-error-contract-") as temporary:
            log_path = Path(temporary) / "gateway.log"
            try:
                for handler in (Bridge, Provider):
                    peer = ThreadingHTTPServer(("127.0.0.1", 0), handler)
                    thread = threading.Thread(target=peer.serve_forever, kwargs={"poll_interval": 0.05})
                    thread.start()
                    peers.append(peer)
                    threads.append(thread)
                gateway_port = port()
                config = configuration(rendered, gateway_port, peers[0].server_port,
                                       peers[1].server_port, processor_port)
                with log_path.open("w") as log:
                    process = subprocess.Popen([str(binary.resolve()), "-c", json.dumps(config)],
                                               stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                               env={"PATH": os.defpath, "HOME": temporary})
                    for _ in range(100):
                        self.assertIsNone(process.poll(), log_path.read_text())
                        try:
                            with socket.create_connection(("127.0.0.1", gateway_port), timeout=0.1):
                                break
                        except OSError:
                            await asyncio.sleep(0.05)
                    else:
                        self.fail("gateway did not start: " + log_path.read_text())

                    def open_response(credential="allowed", path=PATH):
                        connection = http.client.HTTPConnection("127.0.0.1", gateway_port, timeout=TIMEOUT)
                        connections.append(connection)
                        connection.putrequest("POST", path)
                        connection.putheader("content-type", "application/json")
                        connection.putheader("x-neurwerk-authorization", f"Bearer {credential}")
                        connection.putheader("x-request-id", "caller-spoof")
                        connection.putheader("x-request-id", "second-spoof")
                        payload = json.dumps(BODY).encode()
                        connection.putheader("content-length", str(len(payload)))
                        connection.endheaders(payload)
                        return connection.getresponse()

                    def first_event(response):
                        lines = []
                        while line := response.readline():
                            lines.append(line)
                            if line in (b"\n", b"\r\n"):
                                break
                        return b"".join(lines)

                    response = await asyncio.to_thread(open_response)
                    self.assertEqual(response.status, 200, await asyncio.to_thread(response.read) if response.status != 200 else "")
                    request_id = response.getheader("x-request-id")
                    self.assertEqual(str(uuid.UUID(request_id)), request_id)
                    self.assertEqual(request_id, auth_calls[-1][0])
                    self.assertEqual({key.lower(): value for key, value in provider_calls[0].items()}["x-request-id"], request_id)
                    event = await asyncio.to_thread(first_event, response)
                    self.assertIn(b'"content":"first"', event)
                    self.assertFalse(finished.is_set(), "first SSE frame was withheld until EOF")
                    release.set()
                    self.assertIn(b"[DONE]", await asyncio.to_thread(response.read))

                    denied = await asyncio.to_thread(open_response, "denied")
                    self.assertEqual(denied.status, 401)
                    denial = json.loads(await asyncio.to_thread(denied.read))
                    self.assertEqual(denial["error"]["code"], "authentication_failed")
                    self.assertEqual(denial["error"]["request_id"], denied.getheader("x-request-id"))
                    self.assertEqual(denied.getheader("x-request-id"), auth_calls[-1][0])

                    # Exercise application admission, independently of gRPC transport exhaustion.
                    processor._active = settings.grpc_maximum_concurrent_rpcs
                    try:
                        overloaded = await asyncio.to_thread(open_response)
                        self.assertEqual(overloaded.status, 503)
                        overload = json.loads(await asyncio.to_thread(overloaded.read))
                    finally:
                        processor._active = 0
                    self.assertEqual(overload["error"]["code"], "capacity_unavailable")
                    self.assertEqual(overload["error"]["request_id"], auth_calls[-1][0])
                    self.assertEqual(overloaded.getheader("x-request-id"), auth_calls[-1][0])

                    # Native authorization precedes the transformation: this boundary has no public ID.
                    forbidden = await asyncio.to_thread(open_response, "forbidden")
                    self.assertEqual(forbidden.status, 403)
                    self.assertIsNone(forbidden.getheader("x-request-id"))
                    await asyncio.to_thread(forbidden.read)
                    # An admitted path without a route fails later and retains the installed header.
                    missing = await asyncio.to_thread(open_response, "allowed", "/bindings/openai/v1/responses")
                    self.assertEqual(missing.status, 404)
                    self.assertEqual(missing.getheader("x-request-id"), auth_calls[-1][0])
                    await asyncio.to_thread(missing.read)
                    self.assertEqual(len(provider_calls), 1)
                    self.assertEqual(len({request_id for request_id, _ in auth_calls}), len(auth_calls))
                    for request_id, _ in auth_calls:
                        self.assertEqual(str(uuid.UUID(request_id)), request_id)
                    self.assertFalse(errors, errors)
            finally:
                release.set()
                for connection in connections:
                    connection.close()
                if process is not None:
                    process.terminate()
                    try:
                        await asyncio.to_thread(process.wait, timeout=TIMEOUT)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        await asyncio.to_thread(process.wait, timeout=TIMEOUT)
                await server.stop(grace=None)
                await engine.close()
                for peer, thread in zip(peers, threads):
                    await asyncio.to_thread(peer.shutdown)
                    peer.server_close()
                    thread.join(timeout=TIMEOUT)


if __name__ == "__main__":
    unittest.main()
