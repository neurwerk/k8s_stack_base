# Required notice preference contract

This Base level pins Studio API/Web `0.15.1`, extProc `0.16.2`, and the
PostgreSQL-only API-key bridge `0.8.2`. It requires both notice databases,
Studio's private mTLS listener, extProc's preference lookup, and AgentGateway's
trusted API-key credential context. Client values cannot disable these parts.
Older signed Base tags remain the choice for clients that decline this upgrade.

Before this level reconciles, use the global `openbao-stack-setup` `0.2.23`
prerequisite at Tooling commit `2d4af9c757366ecdf573c9aac64614545bf6a5ba`
to provision matching source/provisioner password copies in OpenBao:

| Consumer record | Operations provisioner record |
| --- | --- |
| `frontend-studio/internal:postgresqlPassword` | `infra-postgres-operations/internal:studioPassword` |
| `auth-keycloak-api-key-bridge/internal:postgresqlPassword` | `infra-postgres-operations/internal:apiKeyBridgePassword` |

The required standalone packages `releases/studio/secret-sync` and
`releases/keycloak-api-key-bridge/secret-sync` deliver these as namespace-local
Secrets. They must be composed by the client as **two distinct Flux
Kustomizations**, each checking its two ExternalSecrets and two target Secrets
for readiness. The owning namespaces, External Secrets controller, OpenBao,
and both namespace-local SecretStores must already exist. Stage and verify the
credential-sync Kustomizations **before** allowing the updated infrastructure
or application stage to reconcile. Do not make them depend on the updated
infrastructure stage while that stage waits for their readiness: that forms a
cycle. For an existing alpha installation, hold the updated infrastructure and
applications stages while staging the two standalone packages using the
previously installed SecretStores; resume infrastructure first, then
applications. For a fresh installation, first bring up the prerequisite
SecretStores/OpenBao without starting the new database-dependent releases;
follow the supported bootstrap sequence, then gate the new releases on the
ready standalone packages. The Base stage indexes do not own those packages
and Kustomization directory ordering cannot replace explicit `dependsOn` and
health checks.

PostgreSQL operations provisioning creates and verifies dedicated `studio` and
`api_key_bridge` roles and databases. It refuses collisions or incompatible
ownership markers rather than taking over existing data. The bridge init
container creates or checks its schema; it does not import SQLite data. Preserve
any existing bridge PVC and arrange an explicit data migration if it contains
keys; do not remove the old claim during adoption. Both databases share the
operations instance's backup and recovery point. Verify backups and database
readiness before enabling the consumers.

Studio's private `GET /internal/v1/notice-preferences` serves nine effective
booleans over mTLS using only trusted principal and personal-key identifiers.
The client certificate CN is `monitor-agentgateway-extproc-studio`; the
browser-facing route does not expose this listener. ExtProc uses all-on display
when the bounded lookup fails. Preferences change notice display only; policy
blocks, errors and PII enforcement remain in force. The cert-manager approval
policy already has exact server DNS and client CN policies for these two
certificates.

## Optional personal LLM and MCP activity

`frontendStudio.api.llmLogs.enabled` is false by default. Enabling it requires
a Studio API/Web image with the self-only LLM and MCP activity API and the
Tooling `openbao-stack-setup` `0.2.24` catalog already reconciled. The catalog
copies the canonical Langfuse project keys into dedicated fields of
`frontend-studio/internal`; it refuses conflicting existing copies. The API
chart conditionally creates an ExternalSecret from the Studio namespace's
existing OpenBao SecretStore, mounts only those fields in the API container,
and opens Studio API Pod egress only to the Langfuse web Pod on port 3000.
Disabling it removes the Secret consumer and egress, and the API returns 404.
Studio image `0.13.0` implements the personal LLM and MCP activity API. Reconcile
the `0.2.24` credential catalog before enabling this value for a client.
Operator access to Langfuse outside Studio is separate.

## Optional ContextForge wiring

Enable `frontendStudio.api.contextforge` with the dedicated service account and
`setupConfigMapName`/`catalogConfigMapName` both set to `contextforge-setup`.
Personal connections use the Studio origin plus `/oauth/callback`.
