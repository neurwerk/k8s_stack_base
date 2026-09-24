# Neurwerk Kubernetes Platform

This repository is the public, versioned Kubernetes platform contract for the
Neurwerk stack. It contains owned Helm wrapper charts, reviewed upstream chart
dependencies, Flux `HelmRelease` definitions, platform namespaces and defaults,
and offline validation tooling. It does not contain a complete cluster
configuration, client secrets, or application source code.

## Attachment Policy Source

AgentGateway chart `1.8.0` adds producer support for attachment policy version 4.
Each effective model uses typed `attachments.documents` and `attachments.images`
settings; omitted categories block by default. Version 4 emits `document_modes`
and `image_modes` beside the retained safety and routing maps, and never emits the
legacy `attachment_modes` map. It is source-only until a compatible extProc is
published and pinned in a separately authorized release. Existing policy versions
1 through 3, release manifests, runtime pins and client activation remain unchanged.

LibreChat shared chart `1.7.0` permits WebP uploads only when image uploads use
policy version 4. Version 3 retains its JPEG/PNG/HEIC allowlist, and both versions
retain `imageOutputType: png`.

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
