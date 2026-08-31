# Changelog

All notable aggregate platform changes are documented here. Platform versions
follow Semantic Versioning, but upgrade compatibility is declared separately in
the release manifest and migration notes.

## [Unreleased]

## [0.2.6] - 2026-08-31

### Changed

- Pin PII Engine `0.7.2-cpu` and extProc `0.6.3` so explicit conversations use
  principal- and model-bound stable aliases while headerless requests remain
  unlinkable.
- Preserve provider-owned reasoning as opaque request and response data, retain
  exact function-argument transformation, and accept only the narrow
  AgentGateway `1.4.1` metadata-less terminal callback.
- Pin LibreChat source `14d4f2789d8f1d308713f7acd98fab925f8aa74d` by exact
  image digest with schema `1.3.14`, final title generation, reasoning replay,
  Redis streams, and zero delta coalescing.

### Compatibility

- Fresh installation is supported; upgrades from every earlier platform
  release and downgrade are unsupported.
- No client value or confidential value change is required.
- Recovery remains replacement restore.

## [0.2.5] - 2026-08-31

### Fixed

- Keep Dify migration `1c9ba48be8e4` compatible with PostgreSQL 18 by reusing
  its native `pg_catalog.uuidv7` function, and pin the corrected
  `1.15.0-kc-v14` API image across the API, worker, and beat workloads.

### Compatibility

- Fresh installation and upgrades from `v0.2.0` through `v0.2.4` are
  supported.
- No client value, Secret, credential, or API change is required.
- Failed Dify bootstrap attempts remain retry-safe and require no manual
  function or Alembic repair.

## [0.2.4] - 2026-08-31

### Fixed

- Make Dify migration `4474872b0ee6` retry-safe when its concurrent PostgreSQL
  index committed before a later schema operation failed, and pin the corrected
  `1.15.0-kc-v13` API image across the API, worker, and beat workloads.
- Bound LibreChat's DocumentDB connection pool and concurrent connection setup
  against the shared operations PostgreSQL instance.
- Round every AgentGateway model-catalog override to its supported six-digit
  fractional precision so one invalid rate cannot reject the merged catalog.

### Compatibility

- Fresh installation and upgrades from `v0.2.0`, `v0.2.1`, `v0.2.2`, and
  `v0.2.3` are supported.
- No client value, Secret, credential, or API change is required.
- A failed Dify bootstrap may resume without a manual index or Alembic repair;
  completed Dify migrations remain unchanged.

## [0.2.3] - 2026-08-30

### Fixed

- Allow the exact AgentGateway controller Pod identity to retrieve Keycloak's
  public realm JWKS before pushing fail-closed JWT policy to the data plane.

### Compatibility

- Fresh installation and upgrades from `v0.2.0`, `v0.2.1`, and `v0.2.2` are
  supported.
- No client value, Secret, credential, API, image, or state migration is
  required.

## [0.2.2] - 2026-08-30

### Fixed

- Manage DocumentDB application users through the supported `admin`
  authentication database with the `readWriteAnyDatabase` and `clusterAdmin`
  combination required for secondary write users by DocumentDB `0.116.0`,
  without granting user-management authorization.
- Verify the provisioned LibreChat identity with an authenticated read/write
  operation instead of a connectivity-only ping.
- Verify that the LibreChat DocumentDB credential cannot connect to the Dify,
  Langfuse, or LibreChat RAG PostgreSQL databases.
- Limit the DocumentDB administrator's required PostgreSQL `CREATEROLE` and
  `documentdb_admin_role` delegation capabilities to the user-provisioning
  command, transfer the application grant to the database owner, and verify the
  temporary capabilities are revoked.
- Use POSIX-compatible readiness deadlines for both shared PostgreSQL
  provisioning hooks and retain failed retry Pods for diagnostics.

### Compatibility

- Fresh installation and recovery from failed `v0.2.0` or `v0.2.1`
  `postgres-operations` provisioning are supported.
- LibreChat authenticates through `authSource=admin`; no client value, Secret,
  credential, or PVC replacement is required.
- LibreChat remains the only permitted Mongo-compatible consumer of the shared
  gateway until DocumentDB supports database-scoped write roles.

## [0.2.1] - 2026-08-28

### Fixed

- Initialize the shared operations DocumentDB data directory below the
  filesystem mount root so filesystem metadata such as `lost+found` does not
  prevent first startup.

### Compatibility

- Fresh installation and recovery from the failed `v0.2.0`
  `postgres-operations` initialization are supported.
- No client value, Secret, API, or PVC replacement is required.

## [0.2.0] - 2026-08-27

### Added

- Add optional fail-closed Microsoft Active Directory federation for Keycloak,
  including scoped LDAPS trust, credentials, release ordering, and network
  isolation.
- Price the shared OpenRouter leaderboard catalog, preserve native provider
  attribution, and use route-qualified IDs for callers and Dify's managed model.
- Add dedicated `postgres-auth` and `postgres-operations` packages with retained
  Rook/Ceph storage, namespace-isolated credentials, bounded provisioning, and
  exact consumer NetworkPolicies.

### Changed

- Update all Kubernetes tooling consumers to the published, verified `0.5.4`
  image and make AgentGateway group grants explicitly client-owned.
- Update Keycloak from `26.2.4` to `26.7.2`; this includes the upstream LDAP
  group-filter fix required by the Active Directory authorization boundary.
- Move Keycloak to verified-TLS PostgreSQL 18 in `postgres-auth`.
- Move Dify, Langfuse, LibreChat DocumentDB, and LibreChat RAG persistence to
  `postgres-operations`; Dify uses dedicated PostgreSQL and pgvector databases.
- Remove the dedicated Keycloak, Dify, Langfuse, and LibreChat PostgreSQL
  workloads, Dify Weaviate, and LibreChat FerretDB.
- Expand release image evidence with normalized static and Helm-rendered pins.

### Security

- Keep direct `postgres-operations` PostgreSQL traffic plaintext only inside
  exact NetworkPolicy-selected namespaces and workloads. The DocumentDB gateway
  and `postgres-auth` PostgreSQL traffic use verified TLS.
- Record the temporary, object-scoped privilege-escalation exception required by
  the reviewed upstream DocumentDB local image.

### Compatibility

- Fresh installation is supported after satisfying the declared prerequisites.
- Upgrades from `v0.1.x`, unversioned `main`, and existing database PVCs are
  unsupported. Rebuild an empty environment rather than migrating state.
- Downgrade is unsupported; recovery classification is replacement restore.

## [0.1.3] - 2026-08-25

### Fixed

- Add bounded, functional lifecycle probes for Dify services and datastores,
  and order the plugin daemon after PostgreSQL and Redis.

### Compatibility

- Fresh installation and upgrades from `v0.1.1` and `v0.1.2` are supported
  after satisfying the declared prerequisites.
- No client value, Secret, stateful data, or API migration is required.

## [0.1.2] - 2026-08-24

### Fixed

- Update AgentGateway extProc to `0.6.2` so altered PII placeholders produce
  safe markers without truncating human-readable response streams.

### Compatibility

- Fresh installation and upgrades from `v0.1.1` are supported after satisfying
  the declared prerequisites.
- No client value, Secret, stateful data, or API migration is required.

## [0.1.1] - 2026-08-24

### Fixed

- Fetch the annotated release tag object explicitly before CI verifies its SSH
  signature.

### Compatibility

- Fresh installation is supported after satisfying the declared prerequisites.
- No upgrade from an unversioned revision and no downgrade are supported.

## [0.1.0] - 2026-08-24

### Status

- Withdrawn from client rollout because publication CI could not access the
  annotated tag object. The signed tag remains immutable and must not be moved.

### Added

- Initial versioned platform contract for the current charts, HelmRelease
  packages, prerequisites, and default stage composition.
- Deterministic release manifest and migration-note validation.
- Signed immutable Git release tags with exact per-client Flux selection.
- Dedicated release-signer identity and fingerprint in the release manifest.

### Changed

- Clients select a reviewed `vX.Y.Z` release instead of following mutable
  `base/main`.
- Clients fetch the public platform package anonymously over HTTPS while still
  verifying its signed release tag.

### Compatibility

- Fresh installation is supported after satisfying the declared prerequisites.
- No upgrade from an unversioned revision and no downgrade are supported.

[Unreleased]: https://github.com/neurwerk/k8s_stack_base/compare/v0.2.6...HEAD
[0.2.6]: https://github.com/neurwerk/k8s_stack_base/releases/tag/v0.2.6
[0.2.5]: https://github.com/neurwerk/k8s_stack_base/releases/tag/v0.2.5
[0.2.4]: https://github.com/neurwerk/k8s_stack_base/releases/tag/v0.2.4
[0.2.3]: https://github.com/neurwerk/k8s_stack_base/releases/tag/v0.2.3
[0.2.2]: https://github.com/neurwerk/k8s_stack_base/releases/tag/v0.2.2
[0.2.1]: https://github.com/neurwerk/k8s_stack_base/releases/tag/v0.2.1
[0.2.0]: https://github.com/neurwerk/k8s_stack_base/releases/tag/v0.2.0
[0.1.3]: https://github.com/neurwerk/k8s_stack_base/releases/tag/v0.1.3
[0.1.2]: https://github.com/neurwerk/k8s_stack_base/releases/tag/v0.1.2
[0.1.1]: https://github.com/neurwerk/k8s_stack_base/releases/tag/v0.1.1
[0.1.0]: https://github.com/neurwerk/k8s_stack_base/releases/tag/v0.1.0
