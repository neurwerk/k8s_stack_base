# Changelog

All notable aggregate platform changes will be documented here. Platform
versions follow Semantic Versioning, but upgrade compatibility is declared
separately in each release manifest and migration document.

The initial platform release is `v0.1.0`. It has no predecessor or supported
upgrade path.

## [Unreleased]

## [0.3.7] - 2026-09-15

- Unify supported application administration under `platform-admin`, with the
  subtract-only `authKeycloak.platformAdminRoleExclusions: []` setting (#120).
  Application administration still grants no model/MCP, Kubernetes, root, or
  OpenBao authority. Forgejo inheritance applies only when explicitly enabled.
- Add offline application-access planning and a bounded adapter that derives
  endpoint selection and dependencies from local client/platform values (#124,
  #127). Validation does not enforce runtime network policy or verify DNS.
- Carry trusted API-key principal groups into AgentGateway context and pin
  API-key bridge `0.7.0` (#130, #132). Groups are internal metadata, not additional
  permissions; the immutable key grant remains intersected with current roles.
- Grant DocumentDB's `documentdb_bg_worker_role` `CONNECT` on `postgres` and
  verify denial on managed application databases in provisioning (#135).
  Operations PostgreSQL chart `1.2.2` does not broaden public database access.
- Update Studio to `0.9.1` so failed startup initialization stops the API instead
  of serving requests without its shared HTTP clients (#139).
- Adopt verified PII Engine `0.8.1-cpu` in all three Engine/model-sync CPU defaults
  (#152), including strict streamed Chat `stream_options.include_usage` support.
  Engine chart `1.0.3` and model-sync chart `1.0.2` use appVersion `0.8.1`;
  model bundle pins and CPU device settings are unchanged. The scoped CPU-only
  adoption exception permits proceeding without CUDA or the combined PII GitHub
  Release, not without image verification. CPU source is
  `a0883dee93333f05af5ba19034c34dcc87dbe230`, with verified `linux/amd64` digest
  `sha256:a829987654971c87d802f6ba3059d19caf69bc3e969a95bebee62986355cd76a`
  from [CPU publication job 104388944955](https://github.com/neurwerk/k8s_stack_pii_engine/actions/runs/34971499708/job/104388944955).
- Adopt published extProc `0.7.1` in chart `1.0.5`, appVersion `0.7.1`, from
  source `e50450d50849d9254fb9d87d6090d9cd92f3947e` and verified image digest
  `sha256:59bb5a51192dbbb01ba5cb30c4d4b182c5fde2c3ae8c3193b3eda1cb6de3fbe6`
  ([release](https://github.com/neurwerk/k8s_stack_agentgateway_extproc/releases/tag/v0.7.1)).
  Together these pins include the coordinated 20,000-character tool-description
  fixes from [extProc #23](https://github.com/neurwerk/k8s_stack_agentgateway_extproc/pull/23)
  and [PII #11](https://github.com/neurwerk/k8s_stack_pii_engine/pull/11).
  This supersedes #152's earlier `0.8.0-cpu` adoption; source inclusion and
  verified publication do not establish deployment or end-to-end acceptance.

### Staged Optional Packages

- Add private-first Forgejo hosting with native OIDC, a dedicated operations
  database, retained application storage, and separate namespace/secret/app
  stages (#113). Fix onboarding so its password does not restart the shared
  database (#115), normalize Keycloak mapper defaults without weakening claim
  checks (#117), and deliver OIDC credentials as quoted Helm values (#133).
- Add exact private HTTPS-only Forgejo peers (#137) and bump the Forgejo chart
  to `0.1.1` so Flux does not reuse the older chart artifact (#150). HTTPS peers
  do not grant SSH; existing web-and-SSH peers remain additive and separate.
- Add a static, one-device Forgejo HTTPS WireGuard gateway (#141), optional
  OpenBao/ESO server-key delivery (#143), and offline classification of its UDP
  transport without inventing a browser endpoint or device grant (#147).
  Defaults remain disabled, zero replicas and no peers; manual stop/update/start
  and explicit network, identity and packet-path acceptance remain required.
- Add optional on-demand maintenance setup (#148), using maintenance chart
  `0.1.1` and verified Tooling `0.6.2`. Existing initialization images are
  unchanged. The disabled-by-default setup provides static resources and inert
  templates; the operator owns temporary runtime resources. It neither activates
  maintenance nor establishes application quiescence or database backup safety.
- Forgejo, WireGuard (including key delivery), and maintenance packages remain
  excluded from stable eligibility and outside default stages. Source inventory
  and alpha acceptance do not promote them. LibreChat RAG and Code Interpreter
  also remain excluded; this release does not authorize their selection.

### Upgrade Notes

- Stable upgrades are supported, including `v0.3.6` to `v0.3.7`, subject to
  reviewed client values, backup/restore evidence and the migration gates.
  Skipped-version upgrades must apply every crossed release's instructions.
  No alpha promotion revisions are declared: the existing alpha environment
  remains permanently alpha, not a candidate for promotion to this stable tag.
- Review the effective application administrator grants before adopting. If
  narrower inheritance is required, coordinate the approved exclusion list with
  the platform transition; older charts do not enforce the new exclusion key.
  Lists replace rather than merge. Preserve separate explicit model/MCP grants
  and memberships. After realm-role provisioning, verify fresh tokens and
  inherited access before any separately authorized membership cleanup.
  Initial-user provisioning is add-only; exclusions do not revoke direct groups,
  native sessions, tokens, SSH keys or deploy keys.
- Verify operations PostgreSQL provisioning completes, worker connectivity is
  repaired and managed application databases remain isolated. Verify Studio
  startup, bridge authorization and PII Engine/model-sync readiness, including
  streamed usage and 20,000-character tool descriptions through the coordinated
  extProc/PII path. Preserve databases, keys,
  model objects and bundle pins; a passing readiness probe is not data-integrity
  or end-to-end acceptance evidence.
- Baseline prerequisites are unchanged, including schema-4 `openbao-stack-setup`
  `0.2.11` at `5d1a33a938e22e9034581aebecf33485adc88a29`. An already-provisioned
  `v0.3.6` client using only eligible packages needs no blanket OpenBao catalog
  reconciliation for this release. Fresh installations and missing baseline
  prerequisites still require the documented authorized setup.
- Only a separately approved optional Forgejo selection requires its recorded
  `openbao-stack-setup` `0.2.12` at
  `7a00c0d7a725a500ca251d699ce3f00dff57e660`; WireGuard/key delivery requires
  `0.2.13` at `0c0d5b35fc9e68b70e0cb48f3741e1888f7a287b`. These are
  selection-gated schema-4 catalogs, not global prerequisite replacements or
  permission to bypass this release's exclusions. Stage namespaces and values,
  then secret delivery and authorized catalog setup before application startup.
  Maintenance needs its reviewed Traefik/provider and empty-backend acceptance,
  not a new OpenBao catalog ceremony.

### Known Limitations

- AgentGateway may report a partial streamed response as complete after a late
  extProc failure (#108). The rejected chunk was not forwarded and no PII leak
  was demonstrated. This remains accepted only for interactive chat; reassess
  before unattended actions. PII `0.8.1` / extProc `0.7.1` do not resolve it.
- The existing exact LibreChat development-image exception still expires
  `2026-09-30`; neither its digest nor expiry is extended. Replace it with a
  reviewed immutable upstream release before expiry.
- This is draft preparation dependent on unmerged #152. Provenance currently
  records all 17 commits after `v0.3.6` through main `22e834c`, plus both #152
  adoption commits through `67d60207066ec866f23bcd65f8ab36be4025fdf0`.
  #152 was closed unmerged after replacement #156 merged separately. Refresh
  against actual main history before final review; this stack retains the
  requested dependency endpoint. Local checks do not establish alpha
  acceptance of the complete candidate, a tested stable transition, or recovery.

## [0.3.6] - 2026-09-14

- Fix delayed model/MCP streams and incorrect MCP cleanup/error responses.
  Use API-key bridge `0.6.0` and agentgateway_extproc `0.7.0`.
- Update Studio to `0.9.0` with user search and recent sign-in summaries.
- Fix Keycloak's Remember me label contrast with theme `0.1.2`.

### Upgrade Notes

- Gateway MCP is now stateless. Existing clients must initialize again without
  `Mcp-Session-Id`; persistent sessions and standalone event subscriptions are
  unsupported. PII checks remain per call, but a previous blocked MCP call does
  not automatically block later calls. Model conversation handling is unchanged.
- `keycloak-admin` gains `realm-management/view-events`, which grants broader
  event access than Studio's summaries. After role provisioning completes,
  administrators must obtain fresh tokens, for example by signing in again.
- No database migration or PII Engine upgrade is required. Preserve existing data.

### Known Limitations

- An interrupted answer can still look complete (#108). This remains accepted
  for interactive chat; reassess before unattended actions.
- The existing LibreChat image exception still expires `2026-09-30`.
  This release does not upgrade LibreChat or enable Brave by default.

## [0.3.5] - 2026-09-10

- Support company name and PNG/SVG logos in Keycloak.
- Breaking changes: N/a.

## [0.3.4] - 2026-09-09

- Support verified TLS for static MCP servers (`tls: true`, explicit upstream port).
- Update Studio to `0.8.0`: daily model usage, Tokens/USD charts, and self-profile landing.
- Enable LibreChat agents and provision role permissions using the reviewed upstream image.

### Upgrade Notes

- Reconcile operations PostgreSQL before LibreChat's permission hook. Upgrades
  reapply USER/ADMIN agent and marketplace use; only ADMIN may create/share agents.
  Keep `interface.agents` and `interface.marketplace` overrides omitted.
- Verify the hook, application readiness, and existing data. The LibreChat image
  exception still expires `2026-09-30`. See [migration](release/migrations/v0.3.4.md).

## [0.3.3] - 2026-09-09

- Standardize platform-owned access groups and separate application, model, and MCP permissions.
- Define 13 canonical `neurwerk-` groups in
  `charts/keycloak/realm-config/realm-roles/files/standard-access.yaml`; application
  realm roles and composites are unchanged. Seven chart versions advance; runtime
  images, prerequisites, package exclusions, and exceptions remain unchanged.

### Breaking Changes

- Remove `authKeycloak.accessGroups`, `authKeycloak.realmRoles`, and
  `authKeycloak.realmRoleComposites` overrides, including empty keys. Use canonical
  initial-admin and directory memberships, and require
  `openrouterCatalog.grantToAccessGroups: false` in effective client values.
- Grant model and MCP permissions explicitly through
  `authKeycloak.agentgatewayAccessGroups`, only to `/access/neurwerk-llm-all-users`
  and `/access/neurwerk-mcp-all-users` respectively. No application/admin group
  grants, cross-resource grants, or automatic grants for future catalog additions.
- Follow [the migration instructions](release/migrations/v0.3.3.md) for separate
  client/source changes and one-time manual cleanup of superseded unprefixed
  groups in unused development realms. Fresh installs use canonical defaults
  without cleanup. Stable upgrades are supported; alpha promotion and downgrades
  are unsupported. Recovery is forward-fix; no live transition is claimed.

## [0.3.2] - 2026-09-08

- Fix LibreChat MCP authentication with internal routing.
- Preserve required network access in public-DNS mode.

## [0.3.1] - 2026-09-07

### Compatibility

- Support fresh installation only into an independently verified empty or
  replacement environment. Disposable existing data does not establish that
  prerequisite and does not authorize deletion.
- Stable upgrades, alpha promotion, and downgrades are unsupported. Recovery
  requires replacement restore; no in-place migration or live recovery evidence
  is claimed. The signed predecessor is `v0.3.0`.

### Fixed

- Route canonical public endpoint traffic from Dify, LibreChat, and the Keycloak
  initial-administrator email Job through selected Traefik Pods on TCP `443` by
  default, while preserving explicit `public-dns` behavior for clients whose
  canonical endpoints resolve publicly.
- Include routing PR #70 and Studio pin PR #74 through
  `d839c17743dea6ce88c0e83da9a204d4edcecf8a`. Dify API, LibreChat app, and Keycloak
  initial-admin charts advance from `1.0.1` to `1.0.2`.
- Pin Studio API and Web images and application versions to `0.7.1`, fixing
  valid nullable `stream_options` responses being rejected with HTTP 502
  (Studio #14). API chart `1.1.0` advances to `1.1.1`; Web chart `1.0.1` advances
  to `1.0.2`. The historical Studio `0.1.1` to `0.7.0` change was numbering only,
  not a feature rollout. Other image pins and release prerequisites are unchanged.

### Breaking Changes

- Set `canonicalEndpointRouting.mode: public-dns` explicitly for affected
  workloads whose canonical endpoints resolve publicly. The default is
  `internal-traefik`; unknown values fail rendering. In default mode the Keycloak
  action-email Job no longer permits general public HTTPS egress.
- For internal routing, stage client values, adopt the reviewed platform release,
  and verify policies before separately enabling exact CoreDNS rewrites. Do not
  assume the client and platform sources reconcile atomically.

## [0.3.0] - 2026-09-04

This version jump avoids conflicts with versions from the removed historical repository.

### Added

- Persist metadata-only AgentGateway request usage in the operations PostgreSQL
  service and expose authorized usage queries through Studio's private API path.

### Changed

- Move concrete OpenRouter model selection and complete model pricing to
  client-owned ConfigMaps while retaining platform rendering, authorization,
  Dify wiring, limits, and fail-closed validation.
- Generate one LibreChat model-selection contract for grouped model rows,
  endpoint validation, direct-model grouping, and an optional client-owned hard
  default.
- Pin AgentGateway extProc 0.1.3 so PII-disabled full-duplex responses preserve
  provider response bytes.
- Replace future stable-source allowlists with an explicit supported or
  fresh-install-only release policy.

### Compatibility

- Support fresh installation into a verified empty or replacement environment
  and promotion from exact alpha commit
  `dbcac5d1b3069edd5bb65dd57df95dccfba1f6d1`.
- Do not support stable upgrades or downgrades; recovery requires a replacement
  restore.

## [0.1.1] - 2026-09-03

### Changed

- Make installation into a verified empty or replacement environment a platform
  invariant instead of a release-specific compatibility option.
- Update Agentgateway to 1.5.0, including its controller, data plane, Helm
  dependency, and out-of-band CRD prerequisite. The release requires JWT issuer
  and configured audience claims and normalizes cached-token usage accounting.
- Enable LibreChat agent tools with MCP and place custom endpoint reasoning
  options under the configuration block consumed by LibreChat.
- Send the initial Keycloak administrator's required-action email only after
  the public HTTPS issuer is ready, with a configurable 30-minute link lifetime.
- Update the Kubernetes initialization Tooling image to `0.1.1`.
- Update the reviewed LibreChat development snapshot to source commit
  `cdfe54c3498818b21b33fb609fee02f2742b37ea` and its exact multi-architecture
  image digest.
- Pin PII Engine 0.1.1 and Agentgateway extProc 0.1.2 images, including support
  for standard streamed usage request and response shapes.

### Fixed

- Complete signed-tag release publication from the exact tag checkout and bound
  release evidence validation at the released tag.
- Prevent K3s Traefik rolling updates and ServiceLB from competing for host ports
  80 and 443.

### Compatibility

- Support promotion only from alpha commit
  `5a392c5cb4485fb9faef41840c63af1db6aa60fb`; no stable source upgrade or
  downgrade is supported.
- Classify recovery as a configuration revert to the validated alpha commit.
- Continue to support installation into a verified empty or replacement
  environment as a platform invariant.

## [0.1.0] - 2026-09-01

### Added

- Establish the aggregate platform package with immutable application and image
  pins, signed-tag trust, and complete-history provenance.
- Include the namespace, infrastructure, and application packages by default.
- Record optional LibreChat packages and reviewed exceptions explicitly.

### Compatibility

- Fresh installation is supported. Upgrades and downgrades are unsupported.
- Recovery requires replacement restore.
