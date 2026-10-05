# Neurwerk Kubernetes Platform

See [website](https://base.neurwerk.com/) for more information.

## System requirements

The following resources are required to run the stack:

| Resource | Requirement |
|----------|-------------|
| CPU      | 16 vCPUs    |
| Memory   | 64 GB RAM   |
| Storage  | 1 TB        |

A CUDA-compatible GPU with at least **16 GB of VRAM** is recommended for the optional text-to-speech (TTS), speech-to-text (STT), optical character recognition (OCR), and named entity recognition for personally identifiable information (PII NER) features.

## Optional Active Directory stage

`releases/applications` includes the Keycloak server and realm roles, but no longer
includes the Active Directory HelmRelease. A client that needs federation must
explicitly select `releases/keycloak/active-directory` in its own Flux
Kustomization. The release is still `keycloak-active-directory` in `auth-keycloak`
with the same Helm release name and chart. Only one Flux stage may own it.

Order the stages with Flux dependencies and readiness: Base Keycloak/realm roles
Ready, then any selected add-on access release Ready, then Active Directory when
mapping to that add-on's groups. Without add-on mappings, order Active Directory
after Base Keycloak/realm roles. The optional stage needs the existing
namespace-local Keycloak defaults, client values, and runtime Secrets; enable
directory credential delivery before its Job needs the bind Secret. Check the
Active Directory HelmRelease Ready condition in the client's optional stage.

This is **not** automatic compatibility for clients already using Active
Directory. Before adopting a new Base source, review each client's current Flux
inventory and coordinate moving ownership from `applications` to the optional
stage without two owners or accidental pruning/uninstall. Preserve directory
configuration and credentials; verify the provider and its mapped access after
the handoff. A client that does not select the new stage will not reconcile the
Active Directory HelmRelease, including the Job that disables a previously
managed provider. Platform publication, client adoption, and deployment remain
separate approvals.

## Contributing and support

- **Contributions:** Read [CONTRIBUTING.md](.github/CONTRIBUTING.md) before proposing a change.
- **Questions and support:** Use [GitHub Discussions](https://github.com/neurwerk/k8s_stack_base/discussions).
- **Bug reports and feature requests:** Use [GitHub Issues](https://github.com/neurwerk/k8s_stack_base/issues) for reproducible bugs and clearly scoped feature requests.

## Security

Report vulnerabilities privately by following the instructions in [SECURITY.md](SECURITY.md).

## Licensing

Project-owned content is licensed under the [MIT License](LICENSE). Third-party content retains its upstream license.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for provenance and licensing information for vendored charts.
