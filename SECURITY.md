# Security Policy

## Supported Versions

Security fixes target the latest published platform release. Reports against the
current `main` branch are also welcome. Older release tags are not supported;
upgrade to the latest compatible release to receive security fixes. Always read
the target release's compatibility and migration evidence before upgrading.

## Reporting A Vulnerability

Do not open a public issue for a suspected vulnerability. Use
[GitHub private vulnerability reporting](https://github.com/neurwerk/k8s_stack_base/security/advisories/new)
so maintainers can investigate and coordinate disclosure privately.

Include the affected component and version, potential impact, reproduction steps
or a proof of concept, and any known mitigations. Do not include production
credentials, private keys, provider tokens, recovery material, or client data.
Describe how maintainers can reproduce the issue using synthetic data instead.

Maintainers will assess the report, request additional information when needed,
and coordinate remediation and disclosure with the reporter. Keep the report
confidential until maintainers confirm that disclosure is appropriate.

## Scope

This repository owns Helm charts, platform release contracts, namespaces,
platform defaults, and their validation. Reports about an upstream application
or vendored chart are welcome when the platform configuration exposes or
amplifies the issue; maintainers may coordinate with the relevant upstream.

Questions, deployment support, and non-sensitive bug reports belong in
[GitHub Discussions](https://github.com/neurwerk/k8s_stack_base/discussions) or
[GitHub Issues](https://github.com/neurwerk/k8s_stack_base/issues), not private
vulnerability reports.
