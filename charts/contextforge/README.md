# ContextForge foundation

This is the first, disabled foundation package. It is excluded from stable release
eligibility and is not added to default stages or any client. PostgreSQL
provisioning, External Secrets, OpenBao storage, public OAuth callbacks and
integration routing follow separately. Do not enable personal connections yet.

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

`/ready` checks database readiness. Liveness uses TCP because upstream `/health`
returns HTTP 200 even when its response says a dependency is unhealthy. Only
`/tmp` is writable. Application records belong in PostgreSQL, not a local PVC.

## Prerequisites and ordering

Before installing an enabled chart:

1. Apply `releases/namespaces/contextforge`.
2. Provision the dedicated `contextforge` database and role on
   `postgres-operations`, with consumer ingress on destination Pod port `9712`.
3. Apply `releases/contextforge/configuration` and deliver the runtime Secret.
4. Wait for those stages before selecting `releases/contextforge/app`.

The client owns these Flux stages and their `dependsOn` edges. The application
HelmRelease additionally waits for `postgres-operations`. The scaffold does not
yet supply the provisioner or Secret delivery. The client supplies the
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

Secret values must come from OpenBao and External Secrets. Never put them in
Helm values or ConfigMaps. Restarting does not reset an existing administrator's
password. Preserve signing and encryption keys across upgrades and recovery.

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

Ingress allows only AgentGateway and Studio API. Egress allows DNS and operations
PostgreSQL. `providerEgress` adds operator-approved CIDRs and ports. These are IP
rules, not hostname allowlists. Destination ingress and Studio egress must also
permit their respective connections before use. No extProc caller is added.

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
Native Vault wiring is still pending. OAuth client secrets remain in PostgreSQL
upstream; do not claim OpenBao-only storage for every provider credential.

Source contracts: [bootstrap](https://github.com/IBM/mcp-context-forge/blob/077071bbb43599dd5ab9372ebdbb9a8e686a9816/mcpgateway/bootstrap_db.py),
[configuration](https://github.com/IBM/mcp-context-forge/blob/077071bbb43599dd5ab9372ebdbb9a8e686a9816/mcpgateway/config.py),
and [Vault backend](https://github.com/IBM/mcp-context-forge/blob/077071bbb43599dd5ab9372ebdbb9a8e686a9816/mcpgateway/services/token_backends/vault_backend.py).
