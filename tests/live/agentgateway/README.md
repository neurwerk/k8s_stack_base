# Standalone Streaming Regression

Opt-in, standard-library `unittest` for the official AgentGateway **1.5.0 Linux
amd64** executable and Python 3.12+. Supply an absolute `AGENTGATEWAY_BIN` path
outside the checkout; its parent directory must already exist. Download from the
[official release](https://github.com/agentgateway/agentgateway/releases/tag/v1.5.0):

```bash
export AGENTGATEWAY_BIN=/absolute/path/to/agentgateway-linux-amd64
curl --fail --location --proto '=https' --tlsv1.2 \
  https://github.com/agentgateway/agentgateway/releases/download/v1.5.0/agentgateway-linux-amd64 \
  --output "$AGENTGATEWAY_BIN"
printf '%s  %s\n' \
  daca5cda76e8c5ab0c1a75912fecf2d6365095403f810db72029c49d14a37e7b \
  "$AGENTGATEWAY_BIN" | sha256sum --check - && chmod 0755 "$AGENTGATEWAY_BIN"
mise exec -- make streaming-acceptance
```

Direct invocation: `mise exec -- python3 tests/live/agentgateway/test_streaming.py`.
The test verifies SHA256 before execution. Missing, non-executable, or wrong
binaries produce setup failures, never skips. Ordinary `make check` is independent
of this binary and opt-in target.

One test compares native HTTP auth (`extAuthz.protocol.http.metadata`) using
`json(response.body)` with the shared three-field chart schema:
`json(response.headers["x-agentgateway-auth-context"]).<field>` for
`contract_version`, `principal_id`, and `permissions`. This is a schema-level
fixture, not a chart render or a replacement for chart authorization type tests.
It sends an actual 65,536-byte auth response header over HTTP/1.1, checks the
extracted identity and permissions, and verifies the internal header is neither
forwarded upstream (including a caller spoof) nor exposed downstream. The HTTP
application upstream emits a sentinel value for the same header so the downstream
absence assertion verifies response-header removal.

The upstream flushes one SSE event, then waits for explicit release. Body-based
extraction must withhold all downstream bytes until EOF; header-only extraction
must deliver headers and the first event before release. Both also run with a
**simulated auth skip, NOT a verified JWT**, to exercise static body dependencies
even when the auth conditional is false.

The same test also runs a native MCP backend with `statefulMode: stateless`
(the standalone equivalent of the chart's global `sessionRouting: Stateless`),
`prefixMode: always`, and `failureMode: failClosed`. With header-based auth it
checks MCP `2025-11-25` initialization without an issued session ID, initialized
notification acceptance, sessionless tool listing and a prefixed tool call,
GET/DELETE without a session returning 405 without reaching the MCP upstream,
and a POST progress notification delivered before the held tool result and EOF.
Neither side's transport receives a gateway-issued MCP session ID. The original
four body/header and auth/skip cases remain unchanged in scope.

**Fixture limit:** there is no extProc in this standalone test, real or mocked.
It deliberately demonstrates that native 1.5.0 stateless initialization also
accepts a caller-supplied `Mcp-Session-Id`; native stateless mode alone is not
header rejection. The platform requires extProc to reject every incoming MCP
session header with HTTP 404 and a fixed safe error. That processor implementation
must be validated independently; this fixture does not certify the integration,
PII session isolation, late-stream-error handling, or actual client compatibility.
Model conversation/session IDs are outside this MCP transport change.

Passing this fixture does not establish image compatibility or deployment readiness.

All requests and fixture servers use loopback, management listeners are disabled,
and subprocesses/servers are disposable with bounded waits. AgentGateway 1.5.0
itself binds its random data port to a wildcard address; the test authorization
restricts sources to `127.0.0.1`. Run on a trusted workstation. No cluster, external
requests, real credentials, or persistent data are used. This is not PII,
application-client, verified-JWT, or in-cluster end-to-end proof.
