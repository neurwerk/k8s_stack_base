# API-key bridge PostgreSQL contract

The chart runs only the verified `0.8.0` bridge image with an operations
PostgreSQL database. It always runs the `keycloak-api-key-bridge-init-db` init
container and reads the password from the namespace-local
`auth-keycloak-api-key-bridge-postgres-secret` (`password` key). SQLite, its
database URL and its PVC are no longer chart modes. Legacy client values for
them fail chart rendering; an existing PVC must be retained and handled
separately if it contains keys. No SQLite import is performed.

The bridge runs without managed-key registrations. This chart creates no
managed-key grants or verifier mounts.

Operations provisioning requires the `api_key_bridge` role and database and
reads its own namespace-local `api-key-bridge-postgres-values` Secret. It
refuses an unmarked existing database, an incomplete role/database pair or a
privileged/inherited role. The bridge image checks the existing schema on Pod
start and fails for unknown schemas. See the Studio chart README for the two
required secret-sync packages, credential preparation, and Flux stage order.
