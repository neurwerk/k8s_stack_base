# Rejected request debug export

`rejectedRequestDebug.enabled` defaults to `false`. Supply this canonical setting
through `client-values` to both extProc and Langfuse; do not override it separately
in product values. `captureEnabled` defaults to `true` within that lifecycle gate.
Activation requires a published capture-capable extProc image, reconciled OpenBao
debug credentials, successful debug-project initialization, and verified cleanup.
The chart pins the published capture/retention-capable extProc 0.17.0 image.
Credential preparation uses standalone `openbao-stack-setup` 0.2.28 from Tooling
commit `e0146e229ee61839b37785dfa3f509768b2276fd`, including
`reconcile --prepare-rejected-request-debug` while the feature is disabled.
The operator performs its two-custodian reconciliation before activation.

When enabled, `monitorAgentgatewayExtproc.rejectionLangfuse` configures direct
OTLP HTTP export to
`http://monitor-langfuse-web.monitor-langfuse.svc.cluster.local:3000/api/public/otel/v1/traces`.
The endpoint is restricted to that internal listener to match the scoped network
rules. Defaults are `maxCaptureBytes: 65536`, `queueCapacity: 32`, and
`timeoutSeconds: 2`; bounds are 1–1048576 bytes, 1–128 queue entries, and a timeout
greater than zero and at most 10 seconds. Internal HTTP follows the existing
Langfuse transport; NetworkPolicy restricts peers but does not encrypt content.

The feature creates a dedicated ServiceAccount and SecretStore, both named
`monitor-agentgateway-extproc-rejection-debug`, in `monitor-agentgateway-extproc`.
The store uses OpenBao Kubernetes auth role
`monitor-agentgateway-extproc-rejection-debug`, the `openbao` audience, and the
existing `infra-openbao-ca-bundle`. Trusted setup must bind that exact namespace
and ServiceAccount and permit read-only access to only
`secret/data/monitor-agentgateway-extproc/langfuse-debug` (and its corresponding
metadata path when required).

The ExternalSecret `monitor-agentgateway-extproc-rejection-debug` reads
only `publicKey` and `secretKey` from KV `monitor-agentgateway-extproc/langfuse-debug`,
creating Secret keys `public-key` and `secret-key`. These must belong to the
separate debug project, never the normal trace project. Only the extProc exporter
receives them through `secretKeyRef`; the normal collector and Studio do not.
Credential rotation triggers the existing Secret reloader.

`enabled: false` renders no feature ServiceAccount, SecretStore, ExternalSecret,
exporter environment, or Langfuse network allowance. To stop recording safely,
keep `enabled: true` and set `captureEnabled: false` until existing debug data has
expired and cleanup has been verified. Only then disable the entire lifecycle.
Remote capture is independent of
`monitorAgentgatewayExtproc.rejectionCapture`, which remains the local temporary
file opt-in. Disablement removes the owned runtime credential resources; it does
not delete already stored Langfuse data or the OpenBao source record.

## Restricted project initialization

Langfuse receives its debug key pair through the enabled-only
`monitor-langfuse-rejection-debug` ExternalSecret, using the existing
Langfuse SecretStore and `monitor-langfuse/internal` fields `debugProjectPublicKey`
and `debugProjectSecretKey`. Normal web/worker and collector workloads do not
receive this Secret. The debug organization and project both have fixed ID
`rejected-request-debug`; the organization is separate from normal users.

The post-install/post-upgrade Job starts the same pinned web image with different
headless initialization settings, disables PostgreSQL and ClickHouse migrations,
binds its child server to localhost, verifies the project and organization through
the project API, and stops the child. The selected administrator must already be
an OWNER of the normal organization. Bounded read-only database checks reject
unexpected debug memberships, invitations, project ownership, and public-key
collisions before startup, and recheck afterward. The Job does not repair or
delete unexpected membership. Trusted setup must generate a distinct key pair.

The Job has a 300-second deadline and labels excluded from web Services. It uses
the existing narrowly scoped PostgreSQL provisioning peer plus private ClickHouse,
Redis and web access. extProc's HelmRelease depends on Langfuse, whose completed
initialization hook is part of release readiness; Langfuse depends only on shared
infrastructure. Missing runtime debug credentials disable export in a compatible
runtime rather than preventing the extProc Pod from starting.

The implementation follows Langfuse v4.28.1 `web/src/initialize.ts`,
`web/entrypoint.sh`, `web/Dockerfile`, and `web/src/pages/api/public/projects/index.ts`.
It performs no licensed retention-field database writes. Worker configuration
must explicitly retain `LANGFUSE_MIGRATION_V4_ALLOW_PREVIEW_OPT_IN=true` and
`LANGFUSE_ENABLE_BLOB_STORAGE_FILE_LOG=true`, and must not exclude the debug project
through `DELETE_SKIP_PROJECT_IDS`; enabled chart validation enforces this.
If common or worker `additionalEnvFrom` is configured, also supply an explicit
string `DELETE_SKIP_PROJECT_IDS` in common or worker `additionalEnv`. Use `""` for
no exclusions, or retain unrelated project IDs. This validated explicit value
overrides imported settings so an imported debug-project exclusion cannot disable
cleanup. Secret/ConfigMap references for the explicit value are rejected.

## Three-day cleanup

The lifecycle gate also creates an hourly UTC cleanup CronJob with
`concurrencyPolicy: Forbid` and a 660-second Job deadline. It runs
`python -m agentgateway_extproc.lib.rejection_retention` from the separately pinned
extProc image, using only debug-project keys and fixed project/organization IDs.
Both names must also equal `rejected-request-debug`. The Pod has no Kubernetes API
token and can reach only DNS and the internal Langfuse web listener.

The helper expires observations older than 72 hours through the public API and
reports failure for partial scans or data overdue by another 24 hours. Deletion
is asynchronous in Langfuse: the hourly schedule is not an exact 72-hour deletion
deadline. Worker preview/deletion and raw-blob tracking flags remain required.
Keep the lifecycle enabled after stopping capture until expiry and cleanup are
verified. Existing Langfuse data, databases and retention SQL are not migrated.

## Normal failure metadata

The normal collector in `charts/langfuse/templates/otel-collector-config-map.yaml`
uses only the `batch` processor; its trace pipeline has no filter requiring LLM
attributes or excluding HTTP errors. AgentGateway's default tracing enables
`randomSampling: true`, and `agentgateway-parameters.yaml` adds no such filter.
Thus emitted DirectResponse/error spans are not filtered by this Base pipeline.
This is configuration evidence, not proof that a particular gateway build emits
every rejection span. Content-free rejection metadata remains the normal-project
contract; this debug gate adds no raw-content attributes to that pipeline.
Verify actual emitted attributes and rejection coverage with the compatible
runtime before activation. Existing successful-request content policy is separate.
