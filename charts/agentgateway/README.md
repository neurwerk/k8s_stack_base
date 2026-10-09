# AgentGateway MCP presets

Enable the built-in Context7, Brave and GitHub definitions through `mcp.catalog.presets`; use `mcp.catalog.custom` for additional servers.
For GitHub, [create an organization-owned App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app) and provide its client ID, Studio callback URL and OpenBao-managed client secret.

Each preset or custom entry accepts `checks: [{name: Check connection, tool: get_me, arguments: {}, display: {label: Connected account, field: login}}]`; configure only approved read-only tools and non-secret arguments, omit `display` for status-only checks, or use `checks: []` to disable them.
Studio runs checks on click through the gateway with the caller's permissions; `display.field` reads a dot-separated JSON field from that result.

## Studio MCP Setup

The shared `mcp.studioSetup.enabled` switch selects Studio-owned publication.
Use it consistently in AgentGateway, ContextForge, Studio and OpenBao values,
with matching Studio images. See [native bootstrap and cutover](../contextforge/README.md#studio-mcp-setup).

Credential policies are part of the installed catalog: Context7 has an optional
shared `CONTEXT7_API_KEY` header, Brave requires a shared upstream environment key,
and GitHub uses each person's native OAuth connection. Studio saves shared keys
at `secret/data/mcp/shared/<id>` with `apiKey`, `version`, `operationId`, and
`kvVersion`. Tooling seeds an absent record with empty `apiKey`, version `initial`
and KV version 1, and prepares the exact-path roles.

The `mcp-shared-delivery` ServiceAccount/SecretStore uses OpenBao audience `openbao`.
ESO writes only `version`, `kvVersion` and `configured` into `mcp-runtime-values`,
watched by Flux and consumed last by the AgentGateway HelmRelease. Key values
never enter those Helm inputs. Each runtime Secret is named
`mcp-key-<sha256(id)[:12]>-<version>` and reads the exact OpenBao KV version.
Brave waits at zero replicas without a key, and uses a `Recreate` rollout with
the `mcp.neurwerk.com/key-version` Pod annotation when it changes.

Context7 uses the controller's private `contextforge-providers` Gateway on port
8080 with `mcp-forward-<hash12>-<version>` backends. Only ContextForge can reach
that listener; only Studio can reach its private admin port 15000 for activation
checks. Studio has only named `get` access to the installed shared-key Deployments
and current versioned `agentgateway.dev/agentgatewaybackends`. Backend access
follows the same runtime version as the route, including header-only catalogs.
The RoleBinding targets `frontend-studio/studio-mcp` in `infra-agentgateway`;
it grants no Kubernetes writes, list access or Secret reads.
