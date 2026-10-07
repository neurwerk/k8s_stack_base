# MCP presets and client extensions (source only)

This is **disabled source preparation**, not a complete deployed path. Existing
MCP destinations and their metadata remain unchanged. ContextForge packages stay
excluded from release eligibility, absent from default stages and unselected.

## One opt-in catalog

Base ships `catalog/presets.yaml` inside this chart: Context7/no-authentication,
Brave/shared-authentication and GitHub/individual-authentication. These are inert
native registration presets, not automatic connections or grants. They contain no
customer endpoint, native UUID, credential or enabled default. Clients own the
selection, endpoint approval, overrides, custom definitions, secret references,
native mappings, privacy settings and explicit permissions in private values.
Ordinary provider additions use configuration, not provider-specific Base code.
A Base release is needed for a new shared capability or shipped standard preset,
not an ordinary client MCP definition using the existing capabilities.

Use **either** legacy `mcp.servers` **or** `mcp.catalog`, never both. There is no
silent migration. Legacy defaults/output remain unchanged (including historical
content tracing on). Every catalog entry needs explicit boolean `enabled`;
disabled or absent selections add no destination. Catalog defaults are PII on and
content tracing off. Selection grants no access: declare the existing
`mcp:<name>:invoke` role and deliberate group grants separately, plus `llm:invoke`.

Example client declaration (leave disabled until operator preparation):

```yaml
mcp:
  enabled: false
  catalog:
    presets:
      context7:
        enabled: true
        contextforge:
          serverId: "0123456789abcdef0123456789abcdef"
          gatewayId: null # Resolve once through the approved operator ceremony.
        registration:
          upstream_url: https://mcp.example.com/mcp
          transport: STREAMABLEHTTP
          approved_tools: [resolve-library-id, query-docs]
          pii_policy: platform-default
      brave: {enabled: false}
      github: {enabled: false}
    custom:
      - name: custom-docs
        displayName: Approved documentation
        enabled: true
        contextforge:
          provider: custom-docs
          authenticationModel: no-authentication
          serverId: "1123456789abcdef0123456789abcdef"
          gatewayId: null
        registration:
          upstream_url: https://docs-mcp.example.com/mcp
          transport: STREAMABLEHTTP
          approved_tools: [lookup]
          pii_policy: platform-default
```

Presets permit display name, privacy, native IDs and registration overrides, but
not a provider/authentication-model change; use a custom entry for that. Custom
entries use the same server shape, including approved static hosts or workloads.
The same exact host allowlist and Secret-backed upstream auth apply to static
custom entries. Native entries require the non-secret registration metadata shown
above; it does not grant upstream network access. Operators must approve native
SSRF allowlists, network egress and trust separately. No caller can supply a URL,
register a destination, discover new tools or manage registration credentials.

One helper normalizes selected presets and custom entries for routes, backends,
secrets, workloads, PII and tracing. The opt-in backend-only ConfigMap
`infra-agentgateway-mcp-catalog` exports `catalog.json` (that same list),
`registrations.json` (native setup projection) and `studio.json` (Studio projection).
For OAuth entries, `studio.json` derives `oauth_authorization_origin` from the
validated authorization URL: HTTPS host, optional non-default port, no path.
Do not keep parallel handwritten server lists. The optional ContextForge chart's
setup Job consumes this generated catalog, verifies its `catalogHash`, and writes
resolved IDs plus `studio.json` to `frontend-studio/contextforge-setup`. Enable each
native route by adding its integration ID to `mcp.contextforgeRoutesEnabled` only
after setup succeeds. The empty default list permits registration without exposing
native routes. Legacy destinations remain independently routable.

Native registration `visibility` accepts `public` (new catalog default) or `team`.
PUBLIC is deliberately accepted **behind private ingress**, within one fixed
operator-owned team; it is not anonymous platform access or native team isolation.
Keep exact approved virtual-server tool membership; never route global `/mcp`.

### Individual-authentication declarations, not activation

Any provider can declare `individual-authentication`; there are no provider-name
branches. Optional `registration.oauth` source metadata uses exactly:

```yaml
oauth:
  authorization_url: https://provider.example.com/authorize
  token_url: https://provider.example.com/token
  client_id: operator-registered-app
  redirect_uri: https://studio.example.com/oauth/callback
  scopes: [read]
  pkce: true
  client_secret_ref: {name: contextforge-oauth-apps, key: github}
```

This describes one operator-registered OAuth app and approved callback, not dynamic
registration, PAT support or another implemented authentication mechanism. Never
put a client secret in Git, native plaintext headers, chart values or logs.
The chart preserves references only. Secret preparation requires workstation
`openbao-stack-setup` `0.2.26` from reviewed Tooling
commit `c19d902964b12af78eec916883a3b9d5f955700b`, approved app registration and
operator-owned secret preparation. Native app registration uses the optional
ContextForge setup Job. This is a source prerequisite, not a CLI bundled in the
unchanged root Tooling image. Shared keys remain in upstream MCP
services (Brave native `auth_type=none`); personal credential writes for no/shared
integrations stay disabled. Ordinary Studio users connect approved accounts only.

The user accepts native DB per-email personal token storage as a temporary path
with native gateway team context ignored. This does not fix OAuthTokenVault #424;
the foundation's default `OAUTH_TOKEN_BACKEND=vault` is **unchanged**. A separately
approved private DB-backend configuration and persistent `AUTH_ENCRYPTION_SECRET`
still need actual ciphertext/restart qualification. Native PUBLIC registration
visibility is not strict team isolation. The known callback-transfer/browser-binding
risk remains accepted; no upstream callback fix is imposed by this catalog task.

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

Provider is a generic identifier. The legacy `mcp.servers` shape still accepts
only no/shared authentication and no native gateway mapping; use an explicit
catalog migration for generic individual declarations and management metadata.
A valid shape is not a qualified native connection; preparation and live checks
below remain required.

Native server IDs must be unique 32-character lowercase hex UUIDs. Source routes
keep exact public `/mcp/<name>` paths, existing `llm:invoke` plus
`mcp:<name>:invoke` grants and personal API-key grant intersection. Native upstream
MCP is `Stateless` at the fixed private
`contextforge.contextforge.svc.cluster.local:4444/servers/<serverId>/mcp` path.
No global `/mcp`, native administrator, login, OAuth or credential API is exposed.
Catalog-only `contextforge.gatewayId` is management metadata only; routing still uses
only `serverId`. No UUID is inferred or regenerated from the public permission ID.

## Optional native activation

The coordinated source adopts the verified bridge `0.8.2` and extProc `0.16.2`
immutable image pins, replacing the earlier rendering guard. Defaults remain
disabled, ContextForge packages remain excluded, and no client is selected.
Native targets use HTTPS with exact SNI/SAN
`contextforge.contextforge.svc.cluster.local` and the same-namespace
`infra-openbao-ca-bundle:ca.crt` ConfigMap. There is no insecure verification
fallback. Prepare the certificate, CA delivery and native trusted-proxy profile
before selecting these routes. Verified Studio `0.15.1` images and operator
preparation are separate prerequisites for personal connections.

The producer reads nullable `account_email` only from the bridge's
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

The optional ContextForge trusted-proxy profile is explicitly selected; it does
not change the foundation ConfigMap or grant ordinary administrator access.
Before separately approved activation, prepare the
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

### Retaining shared-credential upstream workloads

When moving a workload-backed route to the native catalog, move its existing
`{name, port, path, workload}` definition to `mcp.upstreamWorkloads` instead of
deleting it. This list creates no routes and must match selected native catalog
entries and their exact `registration.upstream_url`. Deployment, Service and
Secret names stay unchanged. Existing `infraAgentgatewayWrapperSecrets.mcp.<id>`
keys are mounted only by the retained upstream Pod, never attached to the native
backend. Ingress changes from AgentGateway to the exact ContextForge application
Pod identity. Deliver the same list to ContextForge for its matching egress rules.

LibreChat configuration and OIDC callback lists derive stable route IDs from
that same catalog selection; they do not consume native credentials or grant
roles. Explicit client-owned invocation roles and group grants remain required.
OAuth app secrets use only `contextforge-oauth-apps:<integration-id>`, delivered
by the optional `releases/contextforge/oauth-apps` package from the fixed approved
OpenBao `contextforge/provider-apps` record. Its ESO read policy must not be added
to the separate native Vault OAuth token.

The catalog remains the sole definition of approved upstreams and native
server/tool membership. The optional ContextForge setup Job establishes consistent
gateway and server mappings without granting platform access. Studio onboarding,
personal connections and native route activation remain explicit client choices.

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
