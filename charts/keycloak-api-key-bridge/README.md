# API-key bridge PostgreSQL cutover (opt-in)

The default chart uses SQLite and its existing PVC. `authKeycloakApiKeyBridge.postgres.enabled: true`
removes the SQLite URL, `/data` volume and PVC, and supplies
`KEYCLOAK_API_KEY_BRIDGE_POSTGRES_HOST`, `PORT`, `DATABASE`, `USER`, and
`PASSWORD` instead. The password comes from the namespace-local
`auth-keycloak-api-key-bridge-postgres-secret` (`password` key). The default
`0.7.1` bridge image is rejected in this mode. Pin a published, verified image
that implements these exact environment variables, schema creation and health
checks before enabling it; no PostgreSQL image is pinned by this change.

The operations chart independently gates the `api_key_bridge` role and database
with `apiKeyBridge.enabled: true`. Its provisioner takes `password` from
`api-key-bridge-postgres-values` in `infra-postgres-operations`. It refuses an
unmarked existing database, an incomplete role/database pair or a privileged
or inherited role. The provisioner does not initialize the application's tables;
the compatible bridge image's init container creates schema version 3 in an
empty database and verifies an existing compatible schema on each Pod start.
Unknown or incompatible schemas fail; no migration or SQLite import occurs.

The optional `releases/keycloak-api-key-bridge/secret-sync` package delivers
both passwords via namespace-local ExternalSecrets. First arrange for the same
generated value at `auth-keycloak-api-key-bridge/internal:postgresqlPassword`
and `infra-postgres-operations/internal:apiKeyBridgePassword`; ensure the
namespace-local Secrets are ready. Then enable operations provisioning and
verify its Job before selecting bridge PostgreSQL mode. The bridge HelmRelease
depends on the operations HelmRelease. Do not select PostgreSQL mode until the
compatible image is pinned and verified. This cutover deliberately does not
copy old SQLite records; the operator has confirmed there are zero user keys.
Check database-name collisions independently before enabling the provisioner.
Removing the old PVC is a separate data-retention decision.
