# Changelog

All notable aggregate platform changes will be documented here. Platform
versions follow Semantic Versioning, but upgrade compatibility is declared
separately in each release manifest and migration document.

The initial platform release is `v0.1.0`. It has no predecessor or supported
upgrade path.

## [Unreleased]

## [0.1.1] - 2026-09-03

### TODO: Curate Changes

- TODO: Replace this scaffold with reviewed release notes.

### Compatibility

- TODO: Describe exact compatibility and recovery behavior.

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

## [0.1.0] - 2026-09-01

### Added

- Establish the aggregate platform package with immutable application and image
  pins, signed-tag trust, and complete-history provenance.
- Include the namespace, infrastructure, and application packages by default.
- Record optional LibreChat packages and reviewed exceptions explicitly.

### Compatibility

- Fresh installation is supported. Upgrades and downgrades are unsupported.
- Recovery requires replacement restore.
