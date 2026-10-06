# Dormant ContextForge MCP destinations

This is **disabled source preparation**, not a complete deployed path. Existing
MCP destinations and their metadata remain unchanged. ContextForge packages stay
excluded from release eligibility, absent from default stages and unselected.

A dormant declaration uses the stable platform permission ID as `name`, not a
native gateway ID. Keep `mcp.enabled: false` when declaring this source shape:

```yaml
mcp:
  enabled: false
  servers:
    - name: context7
      piiEnabled: true
      contentTracingEnabled: false
      contextforge:
        serverId: "0123456789abcdef0123456789abcdef"
        provider: context7
        authenticationModel: no-authentication
```

The other supported pair is `provider: brave` with
`authenticationModel: shared-authentication`. Brave's key stays in its upstream
service; the native registration has auth type `none`. No individual model is
accepted while [#424](https://github.com/neurwerk/k8s_stack_base/issues/424) blocks
OAuth token-team lookup. Do not expose shared or personal credential writes.

Native server IDs must be unique 32-character lowercase hex UUIDs. Source routes
keep exact public `/mcp/<name>` paths, existing `llm:invoke` plus
`mcp:<name>:invoke` grants and personal API-key grant intersection. Native upstream
MCP is `Stateless` at the fixed private
`contextforge.contextforge.svc.cluster.local:4444/servers/<serverId>/mcp` path.
No global `/mcp`, native administrator, login, OAuth or credential API is exposed.
`gateway_id` belongs only in operator/Studio catalog management, not routing.

## Hard activation guard

Any native entry with `mcp.enabled: true` fails Helm rendering. The current bridge
and extProc image pins **lack this identity feature**. No user compatibility flag
can override the guard. A separate future image-adoption PR must verify published
immutable artifacts containing bridge [#30](https://github.com/neurwerk/k8s_stack_keycloak_api_key_bridge/pull/30)
(`5b3581e2eed0ccae85e601efefbc8a2b0b3203d4`) and extProc
[#74](https://github.com/neurwerk/k8s_stack_agentgateway_extproc/pull/74)
(`4bfb2d0a0231fc56ac8721b277f2016af1d015f3`), adopt the actual pins, and replace
the unconditional guard with the reviewed compatible-runtime contract.
This PR declares no unpublished version or digest.

The dormant producer reads nullable `account_email` only from the bridge's
trusted `x-agentgateway-auth-context` response header, never `response.body` or
caller headers. Validated JWT email is authoritative; missing JWT email cannot
fall back to bridge identity. Trace attribution stays JWT `sub` or bridge
`principal_id`, never email. Only native destinations add `account_email` and
`contextforge: true` to `neurwerk.destination_policy`, for every MCP operation.
Compatible extProc strips caller account headers on every route, forwards
`x-contextforge-account-email` only for native routes, and rejects missing or
invalid email with HTTP 403 `contextforge_account_required`. Caller
`Mcp-Session-Id` rejection remains mandatory.

## Separate native preparation and adoption

This PR does not enable native proxy authentication or replace private REST
administrator authentication. Before separately approved activation, prepare the
protected gateway network, approved provider egress, native accounts/roles and
one fixed non-personal team. Native settings must include:

```text
MCP_CLIENT_AUTH_ENABLED=false
TRUST_PROXY_AUTH=true
TRUST_PROXY_AUTH_DANGEROUSLY=true
PROXY_USER_HEADER=x-contextforge-account-email
MCP_REQUIRE_AUTH=true
REQUIRE_USER_IN_DB=true
MCPGATEWAY_DIRECT_PROXY_ENABLED=false
AUTO_CREATE_PERSONAL_TEAMS=false
```

The foundation ConfigMap keeps proxy trust off. The dangerous global trust flags
require a separately reviewed native configuration boundary, including protection
against untrusted Studio caller headers; do not treat this list as safe REST
administrator replacement. Ordinary accounts must be non-admin with an empty
global role and a team role containing exactly `tools.read`, `tools.execute`,
`servers.read`, `servers.use`, `gateways.read`. Native `DEFAULT_USER_ROLE` and
`DEFAULT_TEAM_MEMBER_ROLE` take prepared role **names**, not IDs. No ordinary
browser/native credential management is granted.

Tooling registration source [#114](https://github.com/neurwerk/k8s_stack_tooling/pull/114)
(`025a2281fc6a4fb284f078dd010015d1ca5041b1`) uses
`reconcile-registrations` with `origin`, `team_id`, `owner_email` and
`registrations[{id,provider,authentication_model,upstream_url,transport,gateway_id,server_id,approved_tools,permission,public_route,pii_policy,content_trace}]`.
Gateway IDs start nullable and resolve through aliases `neurwerk-contextforge-<id>`;
keep the approved native server/tool membership and stable route mapping aligned.
Tool publication and private operator reconciliation remain separate. Studio's
backend-only catalog mapping, catalog/onboarding flags and Connect controls remain
unchanged and disabled; no Studio deployment change or new released CLI
prerequisite is claimed.

## Source qualification only

AgentGateway 1.6.0 [request context](https://github.com/agentgateway/agentgateway/blob/v1.6.0/crates/agentgateway/src/mcp/upstream/mod.rs#L75-L107)
copies current request headers except content length/encoding and caller session
ID. [Stateless handling](https://github.com/agentgateway/agentgateway/blob/v1.6.0/crates/agentgateway/src/mcp/streamablehttp.rs#L154-L239)
creates fresh upstream connections per request; [synthetic initialization](https://github.com/agentgateway/agentgateway/blob/v1.6.0/crates/agentgateway/src/mcp/session.rs#L78-L170)
uses the same caller context for initialize, initialized and call. Thus trusted
extProc-mutated email needs no extra header layer, dynamic backend headers,
passthrough switch, broker or cache. Keep native direct proxy off: normal virtual
server calls enforce database and cached tool membership; the early direct proxy
path does not.

These source checks are not actual two-user overlap, identity or PII runtime
evidence. Runtime publication, native preparation, compatible image adoption,
client selection and authorized runtime verification remain deferred; no staging
or restore rehearsal is imposed.
