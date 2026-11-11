# ContextForge upgrade

## Compatibility and approval

ContextForge's target image and chart version are declared in `charts/contextforge/values.yaml` and `Chart.yaml`. This source batch requires a coordinated Studio API/Web release with native OAuth callback issuer forwarding and removal of legacy discovery/publication support. The currently pinned Studio images are unchanged: **this pairing is not deployment-ready until compatible published images are verified and pinned in a later coordinated change**. Do not infer deployment approval from a source merge or successful static checks.

For the assessed transition from source `a10c655b9fc04d42d2029a565a25bad059409c77` to upstream `v1.1.1` (`dbc643befb6f412e62584179c8f0ebb0207d44fb`), native schema `c7e91a2b4d60` advances through three nullable-column migrations to head `e5c2a7b91f4d`. The native bootstrap/schema-check APIs remain available. Preserve existing databases and encryption keys; no reset, re-registration or personal reconnection is part of this upgrade. Other source schemas need their own assessment.

## Approved short maintenance window

1. Confirm the selected release and compatible published Studio pins, existing backups and the live schema. Confirm managed `setup_mode=studio-v1`; unresolved permanent named-operator grants or a chart-owned installation require an explicit reviewed migration first.
2. Record bounded non-secret before-state fingerprints for native gateway/server/tool IDs, ownership, server enabled state and tool membership, account/team/role IDs, personal-connection identity/status and Studio selected/published tools and binding hashes. Exclude tokens, secrets and personal account values from reports; retain detailed comparisons only in approved private storage.
3. Pause reconciliation that could undo the maintenance stop. Block callers, pause Studio API/publication workers and drain in-flight operations; then scale the native application to zero and wait for its Pods to exit. Keep PostgreSQL and credential services running. These are separately authorized operations, not actions performed by the source change.
4. Only then allow the target Helm pre-upgrade migration Job to run. Deployment `Recreate` happens after that hook and is not sufficient by itself. Require bootstrap and schema-at-head checks to succeed before starting the target application.
5. Adopt the matching charts and Studio API/Web images together while traffic remains blocked. Keep integration IDs, URLs, credential policies, ownership markers and `studio-v1` projection unchanged so durable Studio bindings remain valid. Bootstrap verifies retained registrations and must not overwrite Studio membership or enabled state.
6. Compare the after-state fingerprints with the baseline before reopening traffic. Report every actual missing registration or unexpected ID/membership/selection change, including in development environments; accepted development data-loss risk does not authorize silent loss. Do not recreate or delete objects as a troubleshooting shortcut.
7. Resume workers/reconciliation and callers only after readiness. Inspect Flux sources, Kustomizations, HelmReleases, warning events, application/database health, retained data and bounded logs. Check personal OAuth callback completion, Refresh, Publish, exact selected native membership and an authorized read-only tool call; accept operator-confirmed feature tests.

Recovery is forward-fix. A Git or image downgrade does not reverse database migrations. Stop on incompatible state and obtain explicit approval for repair or native restore rather than deleting registrations, databases, PVCs or keys.
