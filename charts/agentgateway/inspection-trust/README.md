# Public inspection trust

This optional package runs `agentgateway-trust-publish` from the existing Tooling
image in `infra-agentgateway`. Both `agentgateway.inspection.enabled` and
`agentgateway.inspection.trust.enabled` default to false. The publisher uses the
verified Tooling `0.8.0` image pinned by full tag and digest in `values.yaml`.
The validation guard accepts that exact release. Adopt future verified releases
in the runtime values, guard and shared Helm validation fixture together: release
image inventory also renders that fixture.

`release/config.yaml` declares this package as optional/included, with inspection
PKI and Tooling `0.8.0` prerequisites. Initial publication does not depend on
Studio readiness. Package eligibility does not select or activate it for a client.

Select `releases/agentgateway/inspection-trust` separately after the PKI stage,
following the [client staging chain](../inspection-pki/README.md#client-staging).
Its HelmRelease reads namespace-local `client-values` and
`agentgateway-inspection-trust-product-values` in `infra-agentgateway`.

Set `agentgateway.inspection.trust.roots` to an ordered list of
`{id, fingerprintSha256, state}`. IDs select declared PKI generations;
fingerprints are verified lowercase SHA-256 of root DER; states are `active` or
`transitional`. The chart renders a public inventory ConfigMap in
`frontend-studio`; the publisher reads only the named public root exports in
`infra-cert-manager`. It has no Secret access.

## Publication

1. Set operation `initialize` for the first publication after current-generation
   approval and PKI readiness, before Studio advertises trust downloads. The Job
   creates a complete valid `snapshots.json` in
   `frontend-studio/agentgateway-inspection-trust-config-map`. Keep the inspected
   forward listener disabled until Studio has loaded the returned `required_version`.
2. Set operation `stage` after initialization and whenever reviewing a new root
   inventory. Staging preserves the old current pointer and appends new assets.
3. Configure Studio with `STUDIO_GATEWAY_TRUST_REQUIRED_VERSION` from the Job's
   `required_version` result, mount the output directory without `subPath`, and
   roll its replicas. Each replica must refuse trust reads/readiness until its
   validated document includes that version.
4. Set operation `promote` only after Studio has converged. The Job verifies
   every serving endpoint's retained assets before advancing the current pointer.
   The release must not depend on a Studio stage that itself waits for promotion.
   Keep one owner for this HelmRelease throughout initialization, staging and
   promotion; change its operation across reconciliations rather than including
   the same release path in several client stage inventories.

Studio's chart owns the required ingress allowance from namespace
`infra-agentgateway`, Pod label
`app.kubernetes.io/name: agentgateway-inspection-trust`, to API TCP port 4010.
Set exact Kubernetes API egress CIDRs/ports through `kubernetesApiEgress`.
The publisher uses the API service IP and direct Pod IPs, so it needs no DNS egress.

The publisher owns the output ConfigMap; Helm owns only inputs and execution.
Uninstalling this chart does not delete retained public history. Only initialize
has namespace-local ConfigMap create permission; later operations have exact-name
update permission. Promotion adds read-only Studio replica discovery permissions.

Versions are canonical PEM bundle hashes. Current URLs are `no-store`; immutable
asset URLs have a one-day public cache lifetime. History is never automatically
pruned, including expired historical public roots. The publisher refuses more
than 1 MiB, 64 versions or 16 roots per bundle. Explicit root retirement removes it
from current trust, not from retained historical assets.
