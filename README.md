# Neurwerk Kubernetes Platform

This repository is the public, versioned Kubernetes platform contract for the
Neurwerk stack. It contains owned Helm wrapper charts, reviewed upstream chart
dependencies, Flux `HelmRelease` definitions, platform namespaces and defaults,
and offline validation tooling. It does not contain a complete cluster
configuration, client secrets, or application source code.

## Required notice preference rollout

The proposed final release requires PostgreSQL for the API-key bridge and Studio
notice preferences, plus verified credential metadata at the Gateway. Its
`release/config.yaml` and generated `release/manifest.yaml` list the two
standalone secret-sync packages as required; listing them does **not** compose
them into `releases/infrastructure` or `releases/applications`. The client owns
two distinct Flux stages for those packages. Infrastructure alone owns the
existing OpenBao SecretStores; the new stages own only their ExternalSecrets.
Infrastructure first creates the stores (even if its Helm releases are still
pending), then the sync stages wait for Ready stores, current-generation Ready
ExternalSecrets, and all four materialized target Secret names. Applications
depend on infrastructure and both sync stages. Do not make infrastructure wait
for these sync stages: its SecretStores would never be created on a fresh install.

For an existing alpha cluster, merge and reconcile a backward-compatible
preparation change (PR1) with compatible Studio/extProc images and charts
and the standalone sync paths before the mandatory final change (PR2). Confirm
the operator-managed OpenBao password copies, four target Secret names (never
their values), database provisioning, and service readiness. The Gateway's
mandatory credential metadata default in PR2 must not reconcile against an old
extProc consumer or bridge. Do not apply client PostgreSQL role toggles to an
older Base infrastructure chart before credential delivery and verified adoption.
The final chart keeps both roles mandatory; do not disable either to work around
missing prerequisites. The operator confirmed there are no API keys to transfer:
start with an empty PostgreSQL database and remove the old bridge SQLite claim
only as a scoped cleanup after the new bridge is healthy.

## Attachment Policy Source

AgentGateway chart `1.9.0` adds producer support for attachment policy version 4.1
as the exact string `"4.1"`, while retaining versions 1 through 4 unchanged.
Each effective model uses typed `attachments.documents` and `attachments.images`
settings. Version 4.1 adds image inspection and textless-image controls with dense
metadata maps; its defaults remain document-only inspection and blocking textless
images. It is source-only until a compatible extProc is published and pinned in a
separately authorized release. Release manifests, runtime pins and client
activation remain unchanged.

LibreChat shared chart `1.8.0` gives policy 4.1 the same image-upload and WebP
handling as version 4. Version 3 retains its JPEG/PNG/HEIC allowlist, and all
supported image-upload versions retain `imageOutputType: png`.

## Support And Security

- Read [CONTRIBUTING.md](.github/CONTRIBUTING.md) before proposing a change.
- Use [GitHub Discussions](https://github.com/neurwerk/k8s_stack_base/discussions)
  for questions and support.
- Use [GitHub Issues](https://github.com/neurwerk/k8s_stack_base/issues) for
  reproducible bugs and scoped feature requests.
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
- See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for vendored chart
  provenance and licensing.

Owned content is licensed under the [MIT License](LICENSE). Third-party content
retains its upstream license.
