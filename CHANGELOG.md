# Changelog

All notable aggregate platform changes will be documented here. Platform
versions follow Semantic Versioning, but upgrade compatibility is declared
separately in each release manifest and migration document.

The initial platform release is `v0.1.0`. It has no predecessor or supported
upgrade path.

## [Unreleased]

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
