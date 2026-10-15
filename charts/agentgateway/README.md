# AgentGateway

One binding catalog supplies API routes, inspected-proxy routes and Studio views.
The clean-cutover configuration requires the matching extProc, bridge, Studio and
LibreChat changes. The unbound `/v1` API and old MCP routes are removed.

## Model API

```yaml
externalGateway:
  enabled: true
infraAgentgatewayWrapper:
  hostname: gateway.example.test
guardrails:
  llmPolicyEngine:
    enabled: true
authKeycloak:
  agentgatewayClientRoles:
    - llm:invoke
    - model:example-chat:invoke
    - provider:example:company-use
aiGateway:
  enabled: true
  apiPort: 8082
  bindings:
    - id: example-api
      enabled: true
      providerId: example
      provider: OpenAI
      upstreamURL: https://provider.example.test/v1
      credentialSources: [caller_supplied, company]
      authSecret: example-provider-key
      profiles: [openai-chat-completions-v1]
      models:
        - name: example-chat
          upstreamModel: provider-model
          piiEnabled: true
          contentTracingEnabled: false
```

The base URL is `https://gateway.example.test/bindings/example-api/v1`.
Use `X-Neurwerk-Authorization: Bearer <Neurwerk key or Keycloak token>` for identity
and the provider's credential header for personal access. A supplied provider
credential never falls back to company billing. Company/local OIDC clients can
send only `Authorization: Bearer <Keycloak token>`.

Each model requires `llm:invoke` and `model:<name>:invoke`. Company mode additionally
requires `provider:<providerId>:company-use`. Selection does not grant permission.

- `models` declares models directly; `catalogModels` selects canonical catalog IDs.
- `attachments` sets binding defaults; model settings can override them.
- `local: true` requires `credentialSources: [none]` and an approved local target.
- `localReroute` selects an explicit local model for company requests. Its invocation
  permission is also required. Personal credentials cannot follow that transition.
- Unknown bindings, operations and ambiguous model aliases are rejected.

See [`values.yaml`](values.yaml), [`catalog/ai-profiles.yaml`](catalog/ai-profiles.yaml)
and [`templates/_ai-bindings.tpl`](templates/_ai-bindings.tpl) for accepted settings
and limits. API requests require their exact `/bindings/<id>` prefix.

## Inspected proxy

Set `inspect: true` on an HTTPS binding, then configure:

```yaml
aiGateway:
  proxy:
    enabled: true
    hostname: proxy.example.test
    port: 8443
    publicPort: 443
    tlsSecretName: agentgateway-proxy-tls-secrets
    signerSecretName: example-inspection-intermediate
    ingressPeers:
      - namespaceSelector:
          matchLabels: {kubernetes.io/metadata.name: kube-system}
        podSelector:
          matchLabels: {app.kubernetes.io/name: traefik}
```

Traefik passes the exact proxy SNI from public 443 to private 8443. Public proxy TLS
and inspection signing use separate Secrets in `infra-agentgateway`. Install the
published inspection root on clients. Only configured destination hosts are allowed.

Use preemptive Proxy Basic over TLS, with the Neurwerk key as password. Identity
is checked for every decrypted request. CONNECT has no initial 407 challenge.
Browser adapters, opaque continuation, WebSockets and compressed requests remain
unsupported; a working login does not prove those operations work.

## Processing and activity

extProc validates version-5 admission, inspects the buffered request and returns
the canonical selection and actual dispatch target before forwarding. The gateway
uses one upstream attempt. Credentials and restoration maps are excluded from
telemetry; content tracing uses sanitized content before request-local restoration.

Studio reads the rendered catalog and public trust snapshots. Configuration is
separate from observed health. Usage/cost are reported sums with unknown
completeness until completion and original provider counters can be established.

### Model trace content

When model content tracing is enabled, Langfuse input contains `messages` plus
available `tools` and tool-selection settings from the request after HTTP policy
processing and before provider translation. Output contains textual `messages`
and native `tool_calls` (IDs, names and arguments), including assembled SSE calls.
These are recorded observations, not lossless HTTP bodies or proof of tool execution.
Each input/output is a JSON attribute so empty lists and large tool schemas survive
without flattening into many span attributes. An unavailable request body omits
tool context; `tools: []` means a captured request had no `tools` field or an empty
one. Legacy function and provider tool settings are retained separately when present.
Missing native call data is `null`, not an empty list. The existing per-model
content-tracing gate applies to all of this content; MCP tracing is unchanged.

## MCP

Select `mcp.catalog.presets` or `mcp.catalog.custom`. Current MCP URLs are
`/bindings/mcp-<server-id>/mcp`; callers need `llm:invoke` and `mcp:<id>:invoke`.
Only stateless exchanges are supported.

Use `mcp.studioSetup.enabled` consistently across AgentGateway, ContextForge,
Studio and OpenBao. Context7 has an optional shared header key, Brave requires a
shared upstream key, and GitHub uses each user's OAuth connection. Secrets stay
in OpenBao/ESO; catalog and Helm inputs contain only non-secret references.

Optional `checks` select approved read-only tool calls with non-secret arguments.
Studio runs them with the caller's permissions. See
[MCP setup](../contextforge/README.md#studio-mcp-setup) for publication and credential activation.
