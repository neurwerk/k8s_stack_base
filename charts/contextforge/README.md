# ContextForge foundation

These foundation packages remain excluded from stable release eligibility and
absent from default stages and clients. The application is disabled by default.
Dedicated PostgreSQL provisioning, namespace-local External Secrets and native
Vault storage wiring are source-only. Public OAuth callbacks, gateway routes,
Studio, integrations and account/role setup are not part of this change.
Individual connections remain blocked by [#424](https://github.com/neurwerk/k8s_stack_base/issues/424).

## Runtime

The application and migration Job use the same verified upstream image:

```text
ghcr.io/ibm/mcp-context-forge:v1.0.11@sha256:e9639c030162d6cb2f9f188600dd69af8091ec62a7ce307d4a74f4d4f86873df
```

Its `linux/amd64` image revision is
`077071bbb43599dd5ab9372ebdbb9a8e686a9816`. The chart runs one replica and one
Gunicorn worker, uses `Recreate`, and exposes only a private ClusterIP Service.
Native authentication stays enabled; trusted-proxy authentication is not enabled
by this foundation. Operators use a private tunnel and native administrator login.

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
   credential preparation only. Use separately approved, published Tooling that
   contains the source catalog and runtime-token lifecycle below; no released
   prerequisite version is declared here.
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

The merged Tooling catalog is source-only: [#108](https://github.com/neurwerk/k8s_stack_tooling/pull/108)
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
Egress allows DNS, operations PostgreSQL and the exact OpenBao Pod on `8200`.
The secret-sync package supplies OpenBao application ingress; the PostgreSQL
package supplies application and migration ingress. `providerEgress` adds
operator-approved CIDRs and ports; these are not hostname allowlists. Studio
egress and all route selection remain separate future work. No extProc caller,
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
