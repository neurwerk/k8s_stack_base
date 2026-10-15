# AgentGateway

## Opt-in native API bindings and inspected proxy

`aiGateway.enabled` defaults to `false`. The new entry modes require the coordinated
v5 extProc and API-key bridge releases. The catalog reports operation support as
`unknown` until production integration is qualified; enabling values is not that
qualification.

The same `aiGateway.bindings` objects produce native routes, exact model targets,
admission policy and Studio's `frontend-studio/agentgateway-catalog-config-map`
(`catalog.json`). The catalog is also published with proxy mode disabled. Runtime
availability and inspection trust are separate publications owned by their
observers; the gateway chart publishes configured intent.

### Configuration

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
    # Required only when company credentials are selected:
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
      api: true
      inspect: false
      profiles: [openai-chat-completions-v1]
      models:
        - name: example-chat
          upstreamModel: provider-model
          piiEnabled: true
          contentTracingEnabled: false
  proxy:
    enabled: false
```

The new base URL is **`https://gateway.example.test/bindings/example-api/v1`**;
the chat operation is `POST /bindings/example-api/v1/chat/completions`.
Use `X-Neurwerk-Authorization: Bearer <neurwerk-key>` for employee identity and
optionally `Authorization: Bearer <provider-key>` for personal provider credentials.
Both modes use the **same URL and canonical model**. A supplied provider header
selects personal mode, including an invalid, empty or repeated header; rejection
never retries with a company key. Without that header, company mode requires the
provider grant. A personal-only binding rejects a missing provider credential;
a company-only binding rejects supplied provider credentials.

The dedicated entry currently accepts Neurwerk keys only. Provider Authorization,
including a JWT, cannot establish employee identity. Legacy OIDC/key access
continues on `/v1` with its existing company/local semantics and permissions.
GET model listing, embeddings, WebSockets and other unselected operations are
denied on the dedicated entry.

Binding fields:

| Field | Contract |
| --- | --- |
| `id`, `enabled` | Unique DNS label, at most 32 characters; explicit boolean opt-in. At most 8 bindings. |
| `api`, `inspect` | Defaults `true` / `false`; select at least one entry. API URLs require `externalGateway.enabled`. |
| `providerId` | Stable provider ID; required for company/personal credentials. |
| `provider` | Native `OpenAI` (default), `Openrouter`, `Anthropic`, or `Gemini` adapter with a matching profile. |
| `upstreamURL` | Fixed URL without userinfo, query or fragment; credential-bearing targets require verified HTTPS. No dynamic host/fallback. |
| `credentialSources` | A unique list: `[caller_supplied, company]`, either credential mode alone, or `[none]` for local targets. Company requires `authSecret` and `provider:<providerId>:company-use`. Without company mode, `authSecret` is rejected. |
| `local` | Defaults `false`; an explicit approved local target with `credentialSources: [none]`. API-only local targets may use HTTP; inspected targets still require verified HTTPS on port 443. |
| `profiles` | Provider-specific profiles below; only their exact POST operations are admitted. |
| `models` | 1–32 entries with canonical `name` and exact `upstreamModel`. Each needs `llm:invoke` and `model:<name>:invoke`. |
| Model options | `displayName` defaults to `name`; `piiEnabled` defaults `true`; `contentTracingEnabled` defaults `false`. `attachments` and `supportsImages` use the existing typed 4.1 attachment policy. Omission blocks documents/images. |

| Native provider | Profile / approved provider header | Operations |
| --- | --- | --- |
| `OpenAI`, `Openrouter` | `openai-chat-completions-v1`, `openai-responses-v1` / `Authorization` | `/v1/chat/completions`, `/v1/responses` |
| `Anthropic` | `anthropic-messages-v1` / `x-api-key` | `/v1/messages`, with `anthropic-version: 2023-06-01`; no beta headers |
| `Gemini` | `google-generate-content-v1` / `x-goog-api-key` | Inspected `/v1beta/models/<approved-model>:generateContent` and `:streamGenerateContent?alt=sse` |

Google requires `api: false` and `inspect: true`: prefixed `/v1beta` API mounts
fail rendering until separately accepted. Other unknown provider/profile pairs
also fail rendering. Query credentials and unsupported transport/body formats
are rejected by the processor. All operation support remains `unknown` in the
catalog pending production composition and qualification.

The only model aliases are the canonical name and unique upstream model name.
Collisions are rejected. Each canonical model belongs to one provider/upstream
binding, and that binding owns every enabled credential mode. Its public native
model conditionally selects internal targets with the same fixed upstream and
separate credential policies. Personal targets never inherit a company Secret.
Google's URL-based model selection additionally needs an exact native alias
route to those same targets; Studio still publishes one canonical destination.
An overlapping legacy model is represented only by the new catalog row, keeping
legacy permissions from weakening the new company mode. Local targets
must be explicitly marked `local: true`; lack of credentials does not establish
locality. New binding routes have no retries or cross-provider reroutes.

### Inspection

Set `inspect: true` on a binding and configure:

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

Both Secrets are in `infra-agentgateway`. The chart requests a separate public
proxy Certificate using `publicCertificates.useProduction`; reconcile its exact
approval profile before issuance. The API hostname and TLS Secret must be distinct.
The PKI owner supplies the inspection intermediate and root trust publication.
The Gateway keeps inspection port 443 internal. Traefik passes the proxy hostname's
TLS from public `publicPort` to the private `infra-agentgateway-forward-proxy`
Service on `port`; Studio advertises `publicPort`. The frontend CONNECT policy
targets that private port. `ingressPeers` requires explicit namespace and pod labels.

Each inspected binding owns one exact HTTPS upstream hostname on port 443 and
one exact-SNI listener. Inner authority must match that listener (optionally
`:443`). CONNECT authority is not employee identity or destination authority:
the selected fixed upstream is authoritative. There is no raw tunnel or dynamic
fallback for unapproved targets.

The locally proven path uses preemptive `Proxy-Authorization: Basic` on the outer
HTTPS CONNECT request, with the Neurwerk key as the password. Authentication is
repeated for every inner request. The current native Tunnel composition accepts
CONNECT before inner authentication; it does not supply a bridge-backed 407
challenge. Browser bootstrap/challenge behavior is being tested separately.

| Browser application | Login | Inference | Upload | History | Export |
| --- | --- | --- | --- | --- | --- |
| ChatGPT | unknown | unknown | unknown | unknown | unknown |
| Gamma | unknown | unknown | unknown | unknown | unknown |

These website profiles describe pending qualification and do not enable routes.
Restoration is request-local; cross-request history/export restoration is not
implemented by the model API profiles. Client qualification must use its actual
wire endpoint and operation; a ChatGPT login label alone does not select a Codex
route. The normal Responses profile does not enable Codex, compressed bodies,
WebSockets, or opaque continuation passthrough.

### Processing and telemetry

PreRouting extAuth constructs exactly one dedicated identity header; repeated
credential headers fail authentication. It freezes one gateway-generated UUID
and the validated v5 admission. Route-level PostRouting extProc receives a
binding-scoped 4.1 policy and the exact `model_bindings` map, buffers the request,
and fails closed before native model selection.

The processor must return canonical `body.model` and `neurwerk.request_context`
dynamic metadata in the **RequestBody reply**: the unchanged admission plus the
resolved `destination_id` (canonical), `requested_model` (original canonical ID
or approved alias), `resolved_model` (upstream model), PII and tracing policy.
Native authorization compares the complete admission and selected model before
the final literal `upstreamModel` rewrite. The documented empty-terminal-body
metadata exception belongs to the processor's per-stream validation; Base does
not weaken metadata requirements on other callbacks.

PostRouting extProc sees the original API path, including `/bindings/<id>`;
only the native model router subsequently strips that exact mount prefix.
The processor must validate the prefix against the frozen `binding_id`, for API
entry only. Proxy profiles retain their native provider paths. No generic path
acceptance or client-controlled stripping is part of this contract.

Database logging remains `llm: metadata`. The same nonsecret `neurwerk.*`
attributes are written to database logs and tracing: request ID, entry mode,
destination kind/ID, provider, binding, profile, operation, credential source,
policy revision, requested/resolved model, credential ID/kind, billing class,
and token/cost completeness. Company source is `company_api`; other sources are
`unknown`, without guessing ownership or subscription. Billing classification
does not establish usage or pricing completeness. `langfuse.user.id` and native
`agentgateway.user` retain the verified principal.

The Langfuse collector maps the fixed 15-field accounting allowlist to
`langfuse.observation.metadata.neurwerk.*`, which Langfuse v4 stores as top-level
`neurwerk.*` metadata for Studio. Native database/stdout/trace keys retain their
native names. Credential IDs/kinds are not promoted by this mapping. Native 1.6
exports boolean trace additions as strings; the collector converts only exact
`"true"` / `"false"` or boolean values. Missing/malformed flags remain absent.

**All current v5 profiles report unknown completeness.** Native 1.6 fills missing
Chat and Google counters with zero, creates priced zero-cost objects even without
usage, and exposes no reliable SSE terminal marker or Responses in-band status to
these CEL expressions. Buffered Anthropic Messages requires both counters, but a
later full-duplex extProc body rejection or abort can still leave HTTP 200, full
counters and cost in telemetry without a usable failure signal. A stricter
processor cannot recover original counter absence after native normalization or
make its late validation result visible to these expressions.

Both completeness flags are therefore `false` for v5 traffic; legacy/historical
requests keep absent flags. Native reported sums, including zero and nonzero
amounts, remain available separately. Neither apparent zero usage nor explicit
zero catalog rates alone establishes a known zero. Enabling known totals requires
qualified raw-usage presence, successful completion and all applicable prices;
partial cache pricing also cannot establish complete cost. This is a native
evidence limit, independent of billing classification or catalog support status.

The supported native grouping fields are `provider`, `requestModel`,
`responseModel`, `httpStatus`, and `attributes`. Query normalized dimensions as
`{"field":"attributes","key":"neurwerk.entry_mode"}` (similarly for request
ID, canonical destination, binding and operation); top-level `entryMode`,
`requestId` and `operationName` are unsupported. Native `requestModel` is the
upstream model, not the canonical `neurwerk.destination_id`. Native record counts
cover selected LLM records: pre-provider denials and CONNECT are not extra usage
rows, and intermediate retries have no complete ledger. PostgreSQL is the deployed
consumer contract; SQLite's grouped `"1"` / `"0"` flags need separate consumer
compatibility and are not claimed as qualified Studio usage.

Content tracing follows the resolved `content_tracing_enabled` flag and uses
native `llm.prompt` / `llm.completion`, before restoration. The new flow does not
use generic response-body tracing. Credentials, cookies and permission lists
are excluded from these accounting attributes. Native per-attempt usage is not
complete, so this initial flow uses one upstream attempt.

## MCP presets

Enable the built-in Context7, Brave and GitHub definitions through `mcp.catalog.presets`; use `mcp.catalog.custom` for additional servers.
For GitHub, [create an organization-owned App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app) and provide its client ID, Studio callback URL and OpenBao-managed client secret.

Each preset or custom entry accepts `checks: [{name: Check connection, tool: get_me, arguments: {}, display: {label: Connected account, field: login}}]`; configure only approved read-only tools and non-secret arguments, omit `display` for status-only checks, or use `checks: []` to disable them.
Studio runs checks on click through the gateway with the caller's permissions; `display.field` reads a dot-separated JSON field from that result.

## Studio MCP Setup

The shared `mcp.studioSetup.enabled` switch selects Studio-owned publication.
Use it consistently in AgentGateway, ContextForge, Studio and OpenBao values,
with matching Studio images. See [native bootstrap and cutover](../contextforge/README.md#studio-mcp-setup).

Credential policies are part of the installed catalog: Context7 has an optional
shared `CONTEXT7_API_KEY` header, Brave requires a shared upstream environment key,
and GitHub uses each person's native OAuth connection. Studio saves shared keys
at `secret/data/mcp/shared/<id>` with `apiKey`, `version`, `operationId`, and
`kvVersion`. Tooling seeds an absent record with empty `apiKey`, version `initial`
and KV version 1, and prepares the exact-path roles.

The `mcp-shared-delivery` ServiceAccount/SecretStore uses OpenBao audience `openbao`.
ESO writes only `version`, `kvVersion` and `configured` into `mcp-runtime-values`,
watched by Flux and consumed last by the AgentGateway HelmRelease. Key values
never enter those Helm inputs. Each runtime Secret is named
`mcp-key-<sha256(id)[:12]>-<version>` and reads the exact OpenBao KV version.
Brave waits at zero replicas without a key. Managed consumers use a no-surge
`RollingUpdate` rollout (`maxSurge: 0`, `maxUnavailable: "100%"`) with the
`mcp.neurwerk.com/key-version` Pod annotation when the key changes. Studio confirms
activation only after the observed generation, exact replica counts and versioned
Secret reference match the intended credential version.

Context7 uses the controller's private `contextforge-providers` Gateway on port
8080 with `mcp-forward-<hash12>-<version>` backends. Only ContextForge can reach
that listener; only Studio can reach its private admin port 15000 for activation
checks. Studio has only named `get` access to the installed shared-key Deployments
and current versioned `agentgateway.dev/agentgatewaybackends`. Backend access
follows the same runtime version as the route, including header-only catalogs.
The RoleBinding targets `frontend-studio/studio-mcp` in `infra-agentgateway`;
it grants no Kubernetes writes, list access or Secret reads.
