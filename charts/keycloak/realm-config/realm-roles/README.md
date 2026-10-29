# Standard Access Groups

`files/standard-access.yaml` owns the application/administrator groups and
their realm roles and composites, plus three resource-only groups.
All groups are direct children of `/access` and use the `neurwerk-` prefix.
The initial-administrator chart defaults to `/access/neurwerk-platform-admins`.
Application administration does not grant model, MCP or AI application invocation.

`mcp-admin` admits explicit shared-tool discovery in compatible Studio releases.
It is inherited by `platform-admin` unless excluded through
`authKeycloak.platformAdminRoleExclusions`, and can be assigned separately through
`/access/neurwerk-mcp-admins`. Studio admission and explicit MCP invocation grants
are still required; this is not unrestricted native ContextForge administration.

`gateway-admin` admits the AI Gateway configuration and health view in compatible
Studio releases, together with `studio-user`. It is inherited by `platform-admin`
unless excluded and can be assigned through `/access/neurwerk-gateway-admins`.
It does not grant destination invocation, global billing or personal content access.

Clients must omit `authKeycloak.accessGroups`, `authKeycloak.realmRoles`, and
`authKeycloak.realmRoleComposites`; supplying them fails rendering. Configure
memberships through initial-administrator groups or approved federation groups,
not alternate standard mappings.

Clients may grant AgentGateway permissions only through
`authKeycloak.agentgatewayAccessGroups`:

- `/access/neurwerk-llm-all-users`: `llm:invoke`, explicit `model:<id>:invoke`
  roles and explicit `provider:<id>:global-use` roles.
- `/access/neurwerk-mcp-all-users`: `llm:invoke` and explicit `mcp:*:invoke` roles.
- `/access/neurwerk-ai-application-users`: `llm:invoke`, explicit
  `application:<id>:invoke` roles and explicit `provider:<id>:global-use` roles.

All resource groups are created without grants by default. `llm:invoke` is
required for every destination type but alone grants no destination access.
Global-use permits an account choice only alongside the destination grant;
it does not grant any model or application. Wildcards are unsupported.
All groups render an explicit `clientRoles.agentgateway: []` before approved
grants are applied. This clears stale application-group grants and revokes a
resource group's grants when its grant-map entry is removed.
Every grant must be in the effective client-role catalog. Selected OpenRouter
models still derive available model roles; clients declare MCP, application,
provider global-use and custom/local model roles in `authKeycloak.agentgatewayClientRoles`. Base owns no model catalog
or destination grants.

`openrouterCatalog.grantToAccessGroups` defaults to `false` and must remain false.
There are no legacy group aliases or automatic membership migrations. Existing
groups and memberships are not deleted by this chart; adoption of the new names requires
separately coordinated client memberships and operator checks.
Existing key grants remain immutable: adding a user role does not expand a key.
Migration to global-use enforcement must explicitly preserve authorized callers.
