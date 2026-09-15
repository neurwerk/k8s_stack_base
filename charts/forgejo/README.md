# Forgejo

Opinionated first-party chart for Forgejo 15.0.8. It is disabled by default and
renders no resources until `forgejo.enabled: true`. There are no upstream chart
dependencies, free-form app.ini overrides, alternative databases, auth providers,
replica controls, or runner resources.

## Required Integration

Platform integration, not this chart, owns the `forgejo` namespace, Flux release
and ordering, OpenBao/External Secrets, Keycloak client and role mapper, database
provisioning, PostgreSQL ingress, cert-manager approval, and tunnel access.

| Input | Contract |
| --- | --- |
| `forgejo.enabled` | Explicit opt-in; global and chart default false |
| `forgejo.hostname` | One canonical DNS hostname, e.g. `forgejo.example.com` |
| `authKeycloak.hostname`, `authKeycloak.realm` | Established shared facts; issuer is `https://<hostname>/realms/<realm>` |
| `forgejo.oidc.caConfigMap` | Optional namespace-local, client-owned public CA ConfigMap with key `ca.crt`; empty uses system roots only |
| `forgejo.networkPolicy.clients` | List of exact `namespace` and nonempty `podSelector` maps for approved internal HTTPS/SSH consumers; default empty |
| `forgejo.networkPolicy.httpsClients` | Exact `namespace` and nonempty `podSelector` maps for private HTTPS-only peers; default empty, incompatible with an enabled public Gateway |
| `forgejo.networkPolicy.postgresPodSelector` | Operations database Pod labels; default `app.kubernetes.io/name: postgres-operations` |
| `forgejo.networkPolicy.postgresPodPort` | Destination Pod port, default `9712`, NOT Service port `5432` |
| `canonicalEndpointRouting.mode` | `internal-traefik` by default; only DNS, operations PostgreSQL and Traefik TCP 443 egress |
| `forgejo.networkPolicy.keycloakPublicCidrs` | For explicit `public-dns`, reviewed individual Keycloak `/32` or `/128` addresses; never general internet egress |
| `externalGateway.enabled` | False: native private HTTPS. True: public Traefik HTTPS termination to internal HTTP. Never public SSH |
| `publicCertificates.useProduction` | Shared staging/production ClusterIssuer selector; default staging |
| `forgejo.persistence` | Retained RWO Ceph claim, default StorageClass `infra-rook-ceph-rbd`, size `20Gi` |
| `forgejo.resources` | Requests and limits shared by init and application containers |
| `forgejo.recoveryLoginEnabled` | Emergency-only native password login flag; default false |

The namespace-local Secret **`forgejo-runtime`** must deliver exactly these
approved fields from OpenBao record **`forgejo/internal`**:

| Key | Consumer |
| --- | --- |
| `dbPassword` | Native PostgreSQL password URI |
| `oidcClientSecret` | Init-only Keycloak source reconciliation |
| `secretKey` | Stable native encryption key URI |
| `internalToken` | Stable native internal API token URI |
| `oauth2JwtSecret` | Stable native OAuth2/CSRF signing secret URI |
| `lfsJwtSecret` | Stable native LFS signing secret URI |
| `adminPassword` | Init-only creation of `forgejo-recovery`; never reset on restart |

Generate keys using the version's native Forgejo secret formats. The two JWT
secrets must each be 32 random bytes encoded as 43-character **unpadded base64url**,
without a trailing newline. Invalid JWT secrets fail before migration instead of
allowing upstream's silent random regeneration. CLI-only secret fields must be
nonempty, NUL-free UTF-8 without a trailing newline. They are read as literal
arguments by `xargs -0`, never interpreted by a shell. They briefly exist in the
init process argument vector because upstream has no file flag for those commands;
they do not appear in Helm output, scripts, application mounts, or bootstrap logs.

Provision database **`forgejo`** owned by dedicated role **`forgejo`** at
`postgres-operations.infra-postgres-operations.svc.cluster.local:5432`.
This deliberately uses the existing operations-only plaintext PostgreSQL/SCRAM
exception. Reconcile database grants and exact consumer ingress before Forgejo.
`releases/forgejo/secret-sync/postgres.yaml` delivers the provisioning password
through the separate `forgejo-postgres-values` Secret. Enabling Forgejo leaves
the shared PostgreSQL StatefulSet and its watched Secret unchanged, avoiding an
intentional shared PostgreSQL restart during onboarding. This is not a guarantee
of outage-free onboarding; shared-instance backup and recovery gates still apply.
The application and initializer share Pod labels
`app.kubernetes.io/name: forgejo`, `app.kubernetes.io/instance: <release name>`,
and `app.kubernetes.io/part-of: forgejo`.

Keycloak must register confidential client **`forgejo`**, exact callback
`https://forgejo.example.com/user/oauth2/keycloak/callback`, and the canonical
HTTPS origin. Source **`keycloak`** uses OIDC discovery and requests `openid`,
`profile`, and `email`. A flat, multivalued **`forgejo_roles`** claim must contain
**`forgejo-user`** for admission and **`forgejo-admin`** for administrator mapping.
Configure the mapper in the ID token and userinfo as required by upstream.
Admin role assignment must also grant `forgejo-user`. Automatic external account
registration is enabled, local registration and automatic account linking are
not. Existing unexpected auth sources block initialization instead of being
deleted. Human login uses Keycloak; automation uses native Forgejo tokens or SSH
keys. Removing an OIDC role is not revocation of already-issued Forgejo sessions,
tokens, or SSH keys; manage native service credentials independently.

## Transport And Persistence

`forgejo.networkPolicy.httpsClients` grants only TCP `3000` on Forgejo Pods,
behind native HTTPS Service port `443`, without granting SSH `2222`. Each peer
combines its exact namespace and Pod labels in one NetworkPolicy source. An empty
list adds no allowance; selecting peers with `externalGateway.enabled: true`
fails rendering rather than admitting them to the public-mode plaintext backend.
Existing `clients` entries retain their web-and-SSH behavior.

This is only a destination-side building block, not a device gateway or a
restricted-access mode. NetworkPolicies are additive: another rule, including an
overlapping `clients` entry, can still grant SSH. Review all effective selectors
before adoption. A future gateway must enforce per-device grants before SNAT,
restrict its own egress, and demonstrate that the CNI sees the selected gateway
Pod identity; node-source exceptions are not an acceptable substitute. No gateway
peers are selected in platform defaults or client values by this change.

Exactly one explicit Certificate `forgejo-tls` exists whenever enabled, even in
private mode. It requests the canonical hostname, RSA 2048, duration `2160h`, and
rotation `Always` from the selected shared public ClusterIssuer. The public
Gateway reuses that Secret and has **no cert-manager issuance annotations**.
The approval policy must therefore authorize Forgejo while the public Gateway is
disabled. DNS-01 issuance does not require a public application A/AAAA record.

Private Service `forgejo` exposes native HTTPS on `443` (Pod `3000`) and built-in
SSH on `2222`. Approved tunnels must preserve the canonical hostname/SNI and
workstation trust. In private mode a Pod-local host alias resolves the canonical
Forgejo name to loopback and `LOCAL_ROOT_URL` uses that hostname on port `3000`.
This avoids certificate-name mismatches and unintended external callbacks.
The runtime `SSL_CERT_FILE` bundle contains system CAs plus only the optional,
independently verified Keycloak CA bundle selected by `forgejo.oidc.caConfigMap`.
Without that selection, Keycloak must present a system-trusted certificate, such
as its existing production certificate. Staging or private-CA Keycloak requires
the verified issuer CA bundle in the selected ConfigMap's `ca.crt` key before
startup. A missing ConfigMap/key blocks the volume mount; an empty bundle blocks
initialization. The chart never creates this client-owned, nonsecret ConfigMap.
Its trust extends the process trust store, so include only approved CA certificates.
Forgejo's own serving leaf/chain is never added to the trust store and cannot
establish trust for Keycloak's independently issued certificate. No OIDC
verification bypass is configured. Upstream 15.0.8's internal API client
itself uses `InsecureSkipVerify` with `ServerName=DOMAIN`; this chart does not
change upstream code. Browser and integration clients must still verify TLS.
Staging certificates require explicit workstation/client staging trust.

Public mode uses Service HTTP port `80` (Pod `3000`), with only Traefik admitted
for public web traffic; its policy does not admit Traefik to SSH. Internal
consumers switch their transport consistently with this mode. No TCPRoute,
LoadBalancer, NodePort or public SSH listener is created. Standard Kubernetes
NetworkPolicy cannot identify destination hostnames on a shared Traefik Pod or
public IP; the listed peers and ports are the enforceable network boundary.

One UID/GID 1000 application Pod uses a Recreate Deployment and retained
`forgejo-data` PVC mounted at `/data`. Repositories, LFS, attachments, indexes,
queues and Git state remain under that path. Its explicit built-in SSH host key
is `/data/ssh/forgejo.ed25519`, created only when absent. Stable application keys
are supplied by OpenBao, not generated on startup. Config is freshly generated by
upstream `environment-to-ini` into a memory-backed volume on every init attempt,
so deleted settings cannot persist from earlier versions. Preserve the PVC,
database, and all durable secret fields together in recovery; retention is not
backup. Disabling or uninstalling retains the PVC but removes serving resources.

## Lifecycle And Recovery

The same exact rootless image performs a bounded, 300-second initialization:
validate required files, regenerate config, ensure host key, run native migration,
reconcile the exact OIDC source, and create the fixed recovery administrator only
when absent. Native CLI tables are parsed by exact columns and names. Readback
verifies source ID/type/enabled state and recovery identity/admin/active state;
the upstream CLI does not expose the full stored OIDC configuration for readback.
Malformed output, unexpected auth sources, inactive/nonadmin recovery accounts,
or any CLI failure blocks startup without deleting data or resetting passwords.
Kubernetes can retry failed init attempts; each attempt is bounded separately.
Application automatic migrations are disabled. Startup and liveness probes are
local TCP; readiness uses `/api/healthz` with the active transport. An identity
provider outage cannot trigger liveness restarts of a healthy process.

Named Reloader inputs are `forgejo-runtime`, `forgejo-tls`, `forgejo-config`, and
`forgejo-scripts`; Helm configuration changes also alter the Pod checksum.
When selected, the OIDC CA ConfigMap is also a named Reloader input so changes
rebuild the trust bundle on Pod replacement.

For an explicitly authorized emergency, set `forgejo.recoveryLoginEnabled: true`
through the normal release values, reconcile, and use the fixed nonsecret
username **`forgejo-recovery`** through private access with its custodied password.
This temporarily enables native web/password Basic authentication, not local
registration. Recovery initialization still migrates the database, but requires
exactly one existing enabled `keycloak` OAuth2 source and an existing active
`forgejo-recovery` administrator. It skips OAuth create/update and never creates,
promotes, or resets an account. Fresh or partially provisioned installations fail
closed; complete normal provisioning before recovery can be used.

In upstream 15.0.8, `models/auth/source.go` registers OAuth providers during
create/update and propagates registration errors. By contrast,
`services/auth/source/oauth2/init.go:initOAuth2Sources` logs per-provider
registration errors as Critical and returns nil, allowing web startup to continue
after discovery fails. `routers/init.go` treats other OAuth initialization errors
(such as signing-key or database failures) as fatal. Web startup still attempts
discovery and can be delayed until that request fails; recovery does not eliminate
all identity-provider network activity or guarantee its latency. Recovery also
still requires the database, durable secrets, TLS and configured CA mounts.
The CLI only exposes source name/type/enabled and user active/admin state, not the
full stored provider configuration or user login-source binding. Those remain
provisioning/custody invariants, not additional claims of CLI verification.

Disable the flag after recovery and revoke emergency sessions or
tokens. Changing OpenBao `adminPassword` does not rotate an existing account;
use a separately authorized native password-change operation and update custody.

## Source And Validation

Read-only upstream chart reference: `forgejo-helm` commit `fd348cfa`, app `15.0.8`.
This chart does not copy its configuration-key preservation, substring table
matching, or boolean flag handling.

Registry verification on 2026-09-14 returned this OCI index for
`code.forgejo.org/forgejo/forgejo:15.0.8-rootless`:
`sha256:8f97b55ca162ef3b538c6c78a2a077df9f2143c41d80b2bc6b6920f8d430df34`.
Linux amd64 manifest:
`sha256:5c63dc3f2112ca91e2db2b61cfd2851818f5d2e4bd4367b0d7a5f4a63b6c9946`.
The index also contains arm64 and arm/v6. The immutable image is fixed in the
chart helper, not a client override.

```bash
mise exec -- python3 -m unittest discover -s tests/charts/forgejo -p 'test_*.py' -v
mise exec -- helm lint --strict charts/forgejo --values tests/charts/forgejo/enabled.yaml
```

The tests execute the real bootstrap shell against a disposable CLI fixture,
including restart, exact-name, malformed-state, and failure paths. They do not
claim actual-image/database acceptance. Integration must verify OIDC admission,
admin mapping, native Git HTTPS/SSH push and LFS, restart persistence, rotation,
and both transport modes before authorized adoption. No deployment is implied.
