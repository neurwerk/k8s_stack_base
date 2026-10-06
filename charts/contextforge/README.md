# ContextForge foundation

These foundation packages remain excluded from stable release eligibility and
absent from default stages and clients. The application is disabled by default.
Dedicated PostgreSQL provisioning, namespace-local External Secrets and optional
native HTTPS/proxy wiring are source-only. The default foundation still uses
Vault; the optional trusted-proxy flow overrides it with encrypted PostgreSQL.
The Vault path mismatch in [#424](https://github.com/neurwerk/k8s_stack_base/issues/424)
is not fixed. No integration, account or client is selected by these packages.

## Runtime

The application and migration Job use the same verified upstream image:

```text
ghcr.io/ibm/mcp-context-forge:v1.0.11@sha256:e9639c030162d6cb2f9f188600dd69af8091ec62a7ce307d4a74f4d4f86873df
```

Its `linux/amd64` image revision is
`077071bbb43599dd5ab9372ebdbb9a8e686a9816`. The chart runs one replica and one
Gunicorn worker, uses `Recreate`, and exposes only a private ClusterIP Service.
Native authentication stays enabled. Trusted-proxy authentication is disabled by
default. Operators use an approved private tunnel and native administrator login.

`/ready` checks the database only, not OpenBao or runtime-token validity.
Liveness uses TCP because upstream `/health`
returns HTTP 200 even when its response says a dependency is unhealthy. Only
`/tmp` is writable. Application records belong in PostgreSQL, not a local PVC.

## Prerequisites and ordering

Before installing an enabled chart:

1. Apply `releases/namespaces/contextforge`.
2. Apply `releases/contextforge/configuration` and
   `releases/contextforge/secret-sync`, after the shared ESO, OpenBao,
   trust-manager and operations SecretStore resources exist.
3. With the application stage still absent or suspended, stage
   `contextforge.enabled: true` and an explicit `contextforge.platformAdminEmail`
   in `contextforge/contextforge-product-values`, key `values.yaml`. This selects
    credential preparation only. Use the exact workstation CLI source prerequisites
    below; operator credential preparation remains separately authorized.
4. Wait for both ExternalSecrets to be Ready and for the runtime ConfigMap,
   runtime Secret and `infra-openbao-ca-bundle` ConfigMap to exist. Never inspect
   Secret values. The client secret-readiness stage must depend on the stage
   creating the SecretStores, not create and wait on them in a dependency cycle.
5. Select `releases/contextforge/postgres` and wait for
   `HelmRelease/infra-postgres-operations/contextforge-postgres` to be Ready.
   Its generic add-on provisioner owns the dedicated `contextforge` role and
   database, rejects unrecorded existing objects, and verifies isolation. The
   package admits application and migration Pods on destination Pod port `9712`.
6. Only after those stage dependencies succeed, separately authorize selection
   of `releases/contextforge/app` with enabled product values.

The client owns these Flux stages and their `dependsOn` edges. The application
HelmRelease additionally waits for `contextforge-postgres`, which waits for
`postgres-operations`. Helm's pre-install migration cannot use resources created
later by the application chart: its external configuration, Secret and CA must
already exist. No client graph is supplied or changed here. The client supplies the
namespace-local `contextforge-product-values` ConfigMap with `values.yaml`.

Optional application metadata requires workstation `contextforge-setup` `0.1.1`
and `openbao-stack-setup` `0.2.26`, both from reviewed Tooling commit
[`c19d902964b12af78eec916883a3b9d5f955700b`](https://github.com/neurwerk/k8s_stack_tooling/commit/c19d902964b12af78eec916883a3b9d5f955700b).
The optional secret-sync packages require that same OpenBao CLI source. This is
immutable source adoption, not a claim of published PyPI wheels or bundled CLIs.
Root Tooling image `0.7.4` is unchanged and contains neither workstation CLI.
The operator uses the frozen project or approved pinned-Git installation on the
trusted workstation; these manifests perform no OpenBao preparation themselves.

The chart requires `contextforge-runtime` ConfigMap and Secret by default.
The Secret must contain these upstream environment keys:

| Key | Purpose |
| --- | --- |
| `DATABASE_URL` | Dedicated PostgreSQL connection, using `postgresql+psycopg://`. |
| `JWT_SECRET_KEY` | Strong, persistent signing key. |
| `AUTH_ENCRYPTION_SECRET` | Strong, persistent encryption key. |
| `PLATFORM_ADMIN_EMAIL` | Native administrator identity. |
| `PLATFORM_ADMIN_PASSWORD` | Strong initial administrator password. |
| `DEFAULT_USER_PASSWORD` | Strong bootstrap password required by upstream. |
| `VAULT_TOKEN` | Dedicated native OAuth storage token; never an ESO or root token. |

Secret values must come from OpenBao and External Secrets. Never put them in
Helm values or ConfigMaps. Restarting does not reset an existing administrator's
password. Preserve signing and encryption keys across upgrades and recovery.

The initial Tooling catalog merged in [#108](https://github.com/neurwerk/k8s_stack_tooling/pull/108)
at `3545629971492c8c4036e34031dbcc0bc48e1d21`, with token lifecycle
[#110](https://github.com/neurwerk/k8s_stack_tooling/pull/110) at
`2de50e9e9a81038309a54e2dba975660260821ed`. ESO reads explicit fields from
`contextforge/internal`: `postgresqlPassword`, `jwtSecretKey`,
`authEncryptionSecret`, `platformAdminEmail`, `platformAdminPassword`,
`defaultUserPassword` and `vaultToken`. It builds the database URL only in the
runtime Secret. The exact `contextforgePassword` copy in
`infra-postgres-operations/internal` goes to the separate
`contextforge-postgres-values:password` Secret, not the shared database Secret.
This does not cause a shared PostgreSQL credential restart.

Native storage uses HTTPS OpenBao, KV v2 mount `secret`, prefix
`contextforge/oauth`, and boolean `VAULT_TLS_VERIFY=true`. The Tooling-owned
`contextforge-oauth` policy permits data-prefix CRUD and matching metadata DELETE
only; the ESO role reads only the internal record. Tokens are orphan,
nonrenewable and last 30 days. Reconciliation reuses valid tokens and fails closed
on expiry or unverifiable permissions. No token is issued by these manifests.
Rotate at least seven days before expiry using the separately approved
two-custodian `--rotate-contextforge-token` flow with the application HelmRelease
suspended and all application and migration Pods stopped; wait for ESO refresh
before an operator resumes it. No automatic renewal, rotation or restart is added.

The namespace trust label selects the existing trust-manager OpenBao CA bundle.
Both application and migration Pods use the pinned application image in a small
init container to combine its `certifi` public roots with that CA in an ephemeral
volume, mounted read-only by the main container. `SSL_CERT_FILE=/trust/ca.crt`
is HTTPX's supported trust setting, not a native Vault path setting.
The pinned native backend creates `httpx.AsyncClient(verify=True)` with default
environment trust, so this keeps certificate and hostname verification enabled
without removing public-provider roots. See
[HTTPX environment trust](https://www.python-httpx.org/environment_variables/#ssl_cert_file).
After a CA change, stop and restart under approved maintenance to rebuild trust;
this slice adds no Reloader watch or automatic restart.

## Optional private HTTPS and trusted proxy

Set `contextforge.tls.enabled: true` in both the ContextForge product values and
the cert-manager approval-policy values. First reconcile the current-generation
`cert-manager-internal-contextforge-server` approval profile, then select
`releases/contextforge/certificates` and wait for its Certificate to be Ready.
The certificate chart owns Certificate `contextforge`; cert-manager alone owns
Secret `contextforge-tls`. It requests the four exact Service DNS names, the
internal ClusterIssuer, RSA-2048 and 90-day rotation. Do not generate a Secret in Git.

The pinned native `run-gunicorn.sh` supports `SSL=true`, `CERT_FILE` and `KEY_FILE`,
passing them as `--certfile`/`--keyfile` to Gunicorn. The application mounts the
certificate read-only and serves HTTPS on the existing Service and Pod port
4444. Startup/readiness use HTTPS; liveness remains process-local TCP. The
migration Job does not run an HTTPS listener or change database transport/CA
trust. TLS off preserves the existing HTTP rendering. Certificate rotation needs
an approved application restart; no new Reloader namespace watch is assumed.

`contextforge.trustedProxy.enabled: true` requires TLS, prepared native accounts,
`defaultUserRole`, `defaultTeamMemberRole` and exact HTTPS `studioOrigin`.
Both application and migration explicitly override the existing ConfigMap with
trusted-proxy flags, existing-user enforcement, required MCP authentication,
disabled direct proxy/personal teams, and database OAuth token storage. Persistent
signing/encryption keys still come only from the runtime Secret. The role names
must resolve to the reviewed non-inheriting roles; the chart cannot verify native
role permissions or active account state.

For this temporary database flow select
`releases/contextforge/secret-sync/database` **instead of** the legacy secret-sync
path. It preserves the same resources and owners but removes the unused Vault
token field. Never compose both overlapping paths. Stage namespace → configuration,
SecretStores/secret-sync and CA → approval profile and ready certificate → dedicated
PostgreSQL provisioning → application, with client-owned Flux dependencies.
The app HelmRelease deliberately retains its legacy dependency shape so TLS-off
clients do not require the optional certificate release.

The same canonical `mcp.upstreamWorkloads` list must be projected through the
namespace-local `client-values` ConfigMap into ContextForge as well as
AgentGateway. It adds egress only to those workload Pod identities in
`infra-agentgateway` on their declared Pod ports, never a broad provider CIDR.
The list does not create a second route. Keep shared provider keys in the upstream
workload's existing Secret; never configure them as native ContextForge headers.

`studioOrigin`, for example `https://studio.example.com`, makes the native default
OAuth callback `https://studio.example.com/oauth/callback`. Stored gateway OAuth
configuration must use that same callback. Only Studio API's exact public GET
callback route is exposed; native management, login and legacy non-popup callback
routes remain private. Native OAuth still owns PKCE, exchange and storage.
Studio API/Web must use the verified `0.15.1` image pins, including the public
callback middleware exemption; `0.15.0` is not suitable for personal connections.
The pinned callback is not browser/account-bound; that accepted limitation remains.

For selected individual integrations, `releases/contextforge/oauth-apps` extracts
only the fixed `contextforge/provider-apps` record into namespace-local Secret
`contextforge-oauth-apps`. Its fields are approved integration IDs; the canonical
OAuth metadata uses `client_secret_ref: {name: contextforge-oauth-apps, key: <id>}`.
Prepare the provider application/client ID and compile its secret through approved
Tooling first. The ESO `contextforge` role needs read-only access to that exact
record; the separate native Vault OAuth token must not gain this permission.
No provider client ID or secret is supplied by Base. Personal OAuth records are
not extracted by this package.

Before exposing the Studio callback, set `traefik.accessLogs.enabled: false` via
the namespace-local `kube-system/client-values` ConfigMap. This deliberately
disables all edge access logs, including error access logs which otherwise retain
callback query strings; ordinary warning/error application logs remain separate.
The default remains unchanged for clients not selecting sensitive callbacks.

Disabling request/access logging and using native/Gunicorn CRITICAL logs does not prove credential
sanitization. Before individual connections, qualify actual stored ciphertext,
credential-safe logs, per-user token lookup and restart persistence. Public native
gateway/server visibility behind private ingress is intentional; the fixed team
is ownership, not a cross-team isolation claim. Preserve the complete operations
database together with its encryption keys for recovery.

## Migrations and maintenance

The pre-install/pre-upgrade Job runs native bootstrap and verifies schema head.
It has a five-minute deadline and leaves failed Jobs available for inspection.
Application startup skips migrations but still performs native bootstrap checks.

**For upgrades, enter maintenance and stop ContextForge before allowing Helm to
upgrade.** A pre-upgrade Job runs before the Deployment changes; `Recreate` alone
does not stop the old application before migration. Existing web maintenance
overlays do not block MCP entrypoints or background work. Keep MCP traffic blocked
until migrations and application readiness succeed. Downtime is accepted; no
backward-compatible schema transition is promised. Do not reverse a migration by
reverting the image. Restore the complete operations PostgreSQL backup or use a
supported forward fix.

The NetworkPolicy is an earlier hook so migration egress works in the default-deny
namespace on first installation. It remains after success or failure and is
replaced before the next install/upgrade. Helm does not remove this retained hook
policy on uninstall; approved cleanup must remove it separately. The namespace
baseline remains default-deny.

Ingress allows only the namespace-scoped AgentGateway and Studio API identities.
Default egress allows DNS, operations PostgreSQL and the exact OpenBao Pod on `8200`.
The secret-sync package supplies OpenBao application ingress; the PostgreSQL
package supplies application and migration ingress. `providerEgress` adds
operator-approved CIDRs and ports; these are not hostname allowlists. Studio
egress is supplied by its optional API chart wiring; route selection remains a
separate activation step. No extProc caller,
public route or provider allowance is added.

## Integration names

| Authentication model | Initial integration |
| --- | --- |
| `no-authentication` | Context7 |
| `shared-authentication` | Brave |
| `individual-authentication` | GitHub; later, private OIDC MCP servers |

These describe upstream provider authentication, not platform admission. Every
caller still needs the platform permission, and API-key grants still apply.
This chart does not register integrations or grant access.

The supplied configuration uses `MCP_INBOUND_PROTOCOL_MODE=auto` and
`MCP_CLIENT_CONNECT_MODE=auto`. AgentGateway remains extProc's only caller.

Vault cache reads are disabled and the cache size is zero. In this exact upstream
revision, writes immediately evict the entry; disabling reads alone is not enough.
Do not use the separate token-exchange grant, which has no cache-disable setting.
OAuth client-secret configuration copies remain in PostgreSQL upstream; do not
claim OpenBao-only storage for every provider credential. No token-exchange
grant, Connect flow or individual proxy activation is enabled by this wiring.

Source contracts: [bootstrap](https://github.com/IBM/mcp-context-forge/blob/077071bbb43599dd5ab9372ebdbb9a8e686a9816/mcpgateway/bootstrap_db.py),
[configuration](https://github.com/IBM/mcp-context-forge/blob/077071bbb43599dd5ab9372ebdbb9a8e686a9816/mcpgateway/config.py),
and [Vault backend](https://github.com/IBM/mcp-context-forge/blob/077071bbb43599dd5ab9372ebdbb9a8e686a9816/mcpgateway/services/token_backends/vault_backend.py).
