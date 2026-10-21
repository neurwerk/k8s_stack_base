# Operations

## Settings and selection

Use the manifest from the exact selected release to review default packages, optional-package eligibility, prerequisites, and compatibility. Review the actual chart defaults and release definition for each affected product; client overrides can change the resulting deployment.

Charts provide reusable defaults, release definitions provide platform settings, and clients provide environment values and product overrides. Ordinary HelmRelease `valuesFrom` entries are merged in order, with later entries winning; inline `spec.values` overrides them. A reference using `targetPath` can override inline values. Keep values sources in the HelmRelease namespace. The [Studio API release](../../releases/studio/api.yaml) is a concrete example of shared defaults followed by client and product settings.

Client configuration owns source selection and readiness gates. Stage required namespaces, values, secret delivery, database provisioning, identity, and certificate resources before their consumers. Follow the selected package's prerequisites rather than treating the infrastructure and application package lists as execution order. The public [example client](https://github.com/neurwerk/k8s_stack_client_example_com) shows client-side Flux composition.

Use the selected release's standalone setup CLI for OpenBao catalog preparation; its revision is independent of the Tooling container image. Follow [OpenBao operations](https://github.com/neurwerk/docs/blob/main/dev/operations/openbao.md) for bootstrap, reconciliation, and credential changes. Reconcile existing installations rather than bootstrapping them again.

## Adoption and upgrades

Follow the shared [platform upgrade procedure](https://github.com/neurwerk/docs/blob/main/dev/operations/upgrades.md) for signed-tag verification, compatibility review, client adoption, and authorized reconciliation. Read [migration instructions](../../release/migrations) for every crossed release. Package presence, a newer version, or successful chart rendering does not establish upgrade compatibility.

- [Upgrade to 0.3.27](upgrade-0.3.27.md) covers binding routes, setup prerequisites, inspection trust, and the LibreChat ownership crossover.
- [AI endpoints](../endpoints.md) covers configured API prefixes, provider credentials, and inspected-proxy clients.
- [Inspection PKI](../../charts/agentgateway/inspection-pki/README.md) and [trust publication](../../charts/agentgateway/inspection-trust/README.md) describe the separate inspection-root lifecycle.

Keep resource ownership singular during package moves. In particular, LibreChat consumers require a separately selected compatible add-on; the crossover procedure does not migrate existing conversations. Preserve shared databases, credentials, identity, and storage when changing product ownership.

## Verification and recovery

After authorized deployment, use the shared [post-deployment checks](https://github.com/neurwerk/docs/blob/main/dev/operations/upgrades.md#after-deployment) to verify exact Flux source revisions, Kustomizations, HelmReleases, warning events, and affected applications. Verify secret-delivery readiness without exposing values, then check the affected login or gateway path, retained data, and bounded logs. Controller readiness alone does not prove application readiness.

Use the target manifest's declared recovery path and applicable migration procedure. A Git or Helm revert cannot undo database migrations, CRD storage changes, or persistent-data changes. If activation fails, preserve shared state and follow the approved repair or restore procedure; do not assume that selecting an older Base tag is a supported downgrade.
