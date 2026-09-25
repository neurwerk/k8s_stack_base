# Neurwerk Kubernetes Platform

This repository is the public, versioned Kubernetes platform contract for the
Neurwerk stack. It contains owned Helm wrapper charts, reviewed upstream chart
dependencies, Flux `HelmRelease` definitions, platform namespaces and defaults,
and offline validation tooling. It does not contain a complete cluster
configuration, client secrets, or application source code.

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
