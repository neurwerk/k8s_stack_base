# Neurwerk Kubernetes Platform

See [website](https://base.neurwerk.com/) for more information.

## Optional Active Directory stage

`releases/applications` does not include the Active Directory HelmRelease.
Select `releases/keycloak/active-directory` only when federation is needed,
after Keycloak realm roles and any selected add-on access stages are Ready.
Use a client-owned Flux Kustomization with a single inventory owner and an
explicit HelmRelease health check. For existing installations, plan the
handoff in maintenance mode **before** changing the platform source:

1. Inspect the actual HelmRelease, chart, Flux inventories, and external
   Keycloak provider, groups, roles, and approved directory mappings; ensure
   the local break-glass login and current backups are available.
2. Remove the old applications-stage AD health check and do not enable the new
   stage yet. Reconcile this client graph change and confirm applications is
   Ready without that check.
3. Move to a reviewed platform source that omits AD from applications; wait
   until the old Flux inventory drops the HelmRelease and its uninstall has
   completed. Verify external Keycloak state and credential delivery before
   proceeding. Do not run two AD reconcilers or owners at once.
4. Select the optional AD stage in the client graph with `dependsOn` covering
   applications (including realm roles) and all selected add-on access stages;
   include a health check for `HelmRelease/auth-keycloak/keycloak-active-directory`.
   Verify the HelmRelease, provider, approved group mappings, and application
   access before leaving maintenance mode.

The AD chart has only post-install/post-upgrade Jobs, not an uninstall hook:
removing this release does not instruct Keycloak to delete its provider, roles,
or groups. Its ExternalSecret is release-owned and may be removed during the
gap, so confirm credential delivery on reinstall. This is a planned outage,
not authorization to delete Keycloak data. For alpha deployments, coordinate
maintenance before this source change reconciles: a client following `main`
could otherwise prune AD automatically. Signed release publication and stable
client adoption need separate approval.

## System requirements

The following resources are required to run the stack:

| Resource | Requirement |
|----------|-------------|
| CPU      | 16 vCPUs    |
| Memory   | 64 GB RAM   |
| Storage  | 1 TB        |

A CUDA-compatible GPU with at least **16 GB of VRAM** is recommended for the optional text-to-speech (TTS), speech-to-text (STT), optical character recognition (OCR), and named entity recognition for personally identifiable information (PII NER) features.

## Contributing and support

- **Contributions:** Read [CONTRIBUTING.md](.github/CONTRIBUTING.md) before proposing a change.
- **Questions and support:** Use [GitHub Discussions](https://github.com/neurwerk/k8s_stack_base/discussions).
- **Bug reports and feature requests:** Use [GitHub Issues](https://github.com/neurwerk/k8s_stack_base/issues) for reproducible bugs and clearly scoped feature requests.

## Security

Report vulnerabilities privately by following the instructions in [SECURITY.md](SECURITY.md).

## Licensing

Project-owned content is licensed under the [MIT License](LICENSE). Third-party content retains its upstream license.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for provenance and licensing information for vendored charts.
