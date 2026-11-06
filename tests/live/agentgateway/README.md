# Native gateway regressions

Opt-in `unittest` fixtures for the official AgentGateway **1.7.0 Linux amd64** executable and Python 3.12+. Supply an absolute `AGENTGATEWAY_BIN` path outside the checkout; its parent directory must already exist. Download from the [official release](https://github.com/agentgateway/agentgateway/releases/tag/v1.7.0):

```bash
export AGENTGATEWAY_BIN=/absolute/path/to/agentgateway-linux-amd64
curl --fail --location --proto '=https' --tlsv1.2 \
  https://github.com/agentgateway/agentgateway/releases/download/v1.7.0/agentgateway-linux-amd64 \
  --output "$AGENTGATEWAY_BIN"
printf '%s  %s\n' \
  1b2fc01a2de29ca113bbb6258b5cae7b1eb3c724071c09d687a449132f2f42f8 \
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
It deliberately demonstrates that native 1.7.0 stateless initialization also
accepts a caller-supplied `Mcp-Session-Id`; native stateless mode alone is not
header rejection. The platform requires extProc to reject every incoming MCP
session header with HTTP 404 and a fixed safe error. That processor implementation
must be validated independently; this fixture does not certify the integration,
PII session isolation, late-stream-error handling, or actual client compatibility.
Model conversation/session IDs are outside this MCP transport change.

Passing this fixture does not establish image compatibility or deployment readiness.

All requests and fixture servers use loopback, management listeners are disabled,
and subprocesses/servers are disposable with bounded waits. AgentGateway 1.7.0
itself binds its random data port to a wildcard address; the test authorization
restricts sources to `127.0.0.1`. Run on a trusted workstation. No cluster, external
requests, real credentials, or persistent data are used. This is not PII,
application-client, verified-JWT, or in-cluster end-to-end proof.

## Native accounting regression

With the same verified binary, run:

```bash
mise exec -- uv run --frozen python tests/live/agentgateway/test_accounting.py
```

This renders the current chart and evaluates its actual database/stdout telemetry
expressions on native OpenAI and Anthropic responses. It compares reported zero,
nonzero, partial and missing usage, a provider 401, an interrupted response, zero
pricing, absent pricing and historical metadata. Normalized completeness stays
unknown while the native reported sums remain available. This protects against
mistaking normalized/defaulted counters or a priced zero for complete evidence.
The fixture injects synthetic admission metadata directly; it does not qualify
identity, extProc, the collector or a production provider. Its native admin API,
provider and gateway bind loopback; temporary SQLite data is removed on exit.

The subscription-accounting fixture also checks that included Responses usage stays separate from metered OpenAI costs:

```bash
mise exec -- uv run --frozen --with opentelemetry-proto==1.38.0 python tests/live/agentgateway/test_subscription_accounting.py
```

## Native admission and error correlation

```bash
mise exec -- uv run --frozen python tests/live/agentgateway/test_admission.py
```

The admission fixture checks default and override endpoint credentials, discovery, MCP admission, inspected-proxy isolation, permissions, repeated headers, and stripping using rendered policies and a synthetic bridge.

For the error-path fixture, set `EXTPROC_ROOT` to the reviewed extProc checkout and run from Base so Mise supplies Helm:

```bash
mise exec -- uv run --project "$EXTPROC_ROOT" --frozen --extra dev --with pyyaml --with cel-python==0.5.0 python "$PWD/tests/live/agentgateway/test_error_contract.py"
```

This uses the real extProc with a synthetic bridge and provider to check request-ID spoof resistance, streaming, authentication/authorization errors, capacity rejection, and route-not-found correlation. It uses no live Engine or provider.

## Native session grouping

```bash
mise exec -- uv run --frozen --with opentelemetry-proto==1.38.0 python tests/live/agentgateway/test_sessions.py
```

This uses rendered tracing expressions and exported OTLP spans to check repeated sessions, principal separation, Claude/Codex headers, request-ID fallback, and spoofed identity without trusted metadata. It also requires the first SSE event to arrive before upstream EOF. Synthetic post-authentication metadata isolates session grouping from authentication; each request retains its own correlation ID.

See [session behavior and upgrade order](../../../docs/operations/agentgateway-upgrade.md).
