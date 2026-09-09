# Standard Access Groups

`files/standard-access.yaml` owns the 11 application/administrator groups and
their unchanged realm roles and composites, plus two resource-only groups.
All 13 groups are direct children of `/access` and use the `neurwerk-` prefix.
The initial-administrator chart defaults to `/access/neurwerk-platform-admins`.
Application administration does not grant model or MCP access.

Clients must omit `authKeycloak.accessGroups`, `authKeycloak.realmRoles`, and
`authKeycloak.realmRoleComposites`; supplying them fails rendering. Configure
memberships through initial-administrator groups or approved federation groups,
not alternate standard mappings.

Clients may grant AgentGateway permissions only through
`authKeycloak.agentgatewayAccessGroups`:

- `/access/neurwerk-llm-all-users`: `llm:invoke` and explicit `model:*:invoke` roles.
- `/access/neurwerk-mcp-all-users`: `llm:invoke` and explicit `mcp:*:invoke` roles.

Both groups are created without grants by default. `llm:invoke` is required by
AgentGateway for both resource types but alone grants no destination access.
All 13 groups render an explicit `clientRoles.agentgateway: []` before approved
grants are applied. This clears stale application-group grants and revokes a
resource group's grants when its grant-map entry is removed.
Every grant must be in the effective client-role catalog. Selected OpenRouter
models still derive available model roles; clients declare MCP and custom/local
model roles in `authKeycloak.agentgatewayClientRoles`. Base owns no model catalog
or destination grants.

`openrouterCatalog.grantToAccessGroups` defaults to `false` and must remain false.
There are no legacy group aliases or automatic membership migrations. Existing
groups and memberships are not deleted by this chart; adoption of the new names requires
separately coordinated client memberships and operator checks.
