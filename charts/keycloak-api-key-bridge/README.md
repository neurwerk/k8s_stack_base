# API-key bridge PostgreSQL contract

The chart runs only the verified `0.8.0` bridge image with an operations
PostgreSQL database. It always runs the `keycloak-api-key-bridge-init-db` init
container and reads the password from the namespace-local
`auth-keycloak-api-key-bridge-postgres-secret` (`password` key). SQLite, its
database URL and its PVC are no longer chart modes. Legacy client values for
them fail chart rendering; an existing PVC must be retained and handled
separately if it contains keys. No SQLite import is performed.

## Managed registrations (draft; not ready for adoption)

`authKeycloakApiKeyBridge.managedRegistrations` defaults to `[]`. Each entry
references `grantConfigMap`, `grantKey`, `verifierSecret`, and `verifierKey` in
the bridge namespace. The chart mounts those add-on-owned, namespace-local files
and passes a JSON list of `{grant_file, verifier_file}` paths to the bridge.
Base does not create managed grants or verifier Secrets. The add-on must arrange
credential delivery and Flux ordering so every referenced resource exists before
the bridge starts. Missing files prevent the Pod from mounting; invalid files
make validation fail closed. An empty list needs no managed-key volumes.

**Do not merge or adopt this chart yet.** The pinned `0.8.0` image does not
understand `KEYCLOAK_API_KEY_BRIDGE_MANAGED_REGISTRATIONS`. Publish and verify a
compatible image, coordinate removal of the old Base-owned verifier sync and
add-on readiness, then update the image pin in a reviewed change before merge.

Operations provisioning requires the `api_key_bridge` role and database and
reads its own namespace-local `api-key-bridge-postgres-values` Secret. It
refuses an unmarked existing database, an incomplete role/database pair or a
privileged/inherited role. The bridge image checks the existing schema on Pod
start and fails for unknown schemas. See the Studio chart README for the two
required secret-sync packages, credential preparation, and Flux stage order.
