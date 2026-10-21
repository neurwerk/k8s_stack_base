# Upgrade to 0.3.27

Stable upgrades are supported after the steps below. Downgrade is unsupported; recovery is forward-fix. Read the selected signed release's [manifest](../../release/manifest.yaml) and any earlier crossed release instructions. No alpha promotion revisions are declared.

## Prepare

Use the exact standalone `openbao-stack-setup` version and source revision in the manifest. This workstation CLI is separate from the Tooling container. Reconcile the existing credential catalog before activating consumers; preserve valid credentials, OpenBao, shared databases, identity, storage and current independent backups. Do not bootstrap an existing installation again.

Review client overrides against the new defaults, including memory budgets, document-processing concurrency and the shared `reasoning.maxFingerprints` setting. Select only the optional packages and add-ons that the client uses. Keep RAG and code interpreter unselected while excluded by the selected add-on.

## Gateway routes and subscription access

Every configured model needs an explicit `aiGateway.bindings` entry; the unbound `/v1` API has been removed. Update client URLs and LibreChat model configuration together. Preserve canonical model IDs and their PII, attachment and tracing policies. Company-funded requests also require the matching `provider:<id>:company-use` permission.

Approve browser OIDC clients through `aiGateway.approvedOidcClients` for exact enabled binding IDs and the `agentgateway` audience. Selection alone grants no user invocation permissions. Provision the matching add-on OIDC scopes before starting its application.

For included ChatGPT subscription access, select the reviewed Codex Responses binding with `upstreamURL: https://chatgpt.com/backend-api/codex`, `profiles: [openai-codex-responses-v1]`, `credentialSources: [caller_supplied]` and `billingMode: included`; enable `inspect: true` for inspected-proxy access. Keep personal credentials separate from the platform key; personal requests must not fall back to company billing. Use Studio's setup instructions for the supported client.

Before enabling the inspected proxy, reconcile certificate approval, inspection PKI and public-root publication in order. First publication uses `initialize`; later changes use stage → every serving Studio replica loads `required_version` → promote. Verify Studio has loaded the published roots and install the verified inspection root on participating workstations. Only then enable the proxy listener and its exact ingress peers. Proxy TLS and the inspection root are different trust boundaries.

## LibreChat ownership crossover

Base no longer owns LibreChat workloads, product OIDC, access roles or LibreChat-specific database provisioning. Installations using LibreChat must select an independently reviewed immutable LibreChat add-on revision compatible with this Base release. Follow that add-on's crossover instructions.

For an operator-approved hard crossover:

1. Suspend the old application reconciliation and stop LibreChat consumers before changing ownership. Confirm that losing existing conversations is approved; this procedure does not migrate them.
2. Remove only the old product-owned releases and conflicting resources. Preserve shared services, credentials and unrelated data. Any product storage or database deletion requires its own exact resource list and operator approval; deleting a namespace is not required.
3. Stage the add-on namespace, client values and secret delivery. Reconcile the selected setup catalog and verify required Secrets are ready without displaying their values.
4. Reconcile database, access, OIDC and certificate-approval stages before the application. Keep each resource under one owner; update old Base health checks to the new stage graph.
5. Configure explicit database/gateway ingress peers, add-on monitoring and `maintenance.addons` entries. Enable the application only after these prerequisites are ready.

Clients that must preserve conversations need a separately reviewed data migration before this crossover. Other add-ons are not activated automatically.

## Debugging and verification

Initial extProc troubleshooting can use `monitorAgentgatewayExtproc.logging.level: DEBUG` and `monitorAgentgatewayExtproc.logging.errorLevel: debug`. The second setting controls expanded client error diagnostics independently of process logs. The retired rejected-request capture pipeline is not restored by these settings; retain its Langfuse cleanup only while old captured records still require deletion.

Run full Base and selected add-on checks, the client checks and exact-release compatibility validation before adoption. After authorized reconciliation, verify Flux revisions, Kustomizations, HelmReleases, warning events, application login, gateway routes, shared persistent data and bounded logs. Accept operator-confirmed feature tests; controller readiness alone does not prove application readiness.

If activation fails, stop the affected consumers and preserve shared state while applying a reviewed forward fix. Moving the Base tag backward does not reverse database, identity or storage changes.
