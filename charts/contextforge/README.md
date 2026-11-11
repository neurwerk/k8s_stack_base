# ContextForge

The chart runs private ContextForge and a bootstrap Job for installed MCP destinations. The image is pinned in `values.yaml`; native management uses trusted-proxy identity over private TLS, never public ingress.

## Studio MCP Setup

Set `mcp-servers.studioSetup.enabled: true` consistently in AgentGateway, ContextForge, Studio API and OpenBao values when selecting managed integrations. Keep `contextforge.setup.enabled`, trusted-proxy access, Studio connections and onboarding enabled. The setup switch supplies administrator discovery. ContextForge without selected integrations can remain unused with setup disabled; ordinary non-ContextForge AgentGateway backends remain supported.

Charts own installed integrations, stable IDs/URLs, credential policies and checks. Studio/PostgreSQL owns enabled state, selected tools and publication progress. Bootstrap creates gateways as the setup administrator and empty native virtual servers as `contextforge-studio`. Later bootstrap verifies these registrations without replacing tool membership or enabled state. It does not import selections, copy personal connections or publish chart-selected tools.

The fixed team owner's role has exactly `admin.user_management`, `teams.manage_members`, `teams.read`, `gateways.read`, `servers.create`, `servers.read`, `servers.update` and `tools.read`, without inheritance. Setup reconciles this permission set only after checking ownership. Revoked, dormant or unexpected service-account grants fail closed rather than being repaired.

Administrators need Studio admission, `mcp-admin`, verified email, `llm:invoke` and the integration's `mcp:<id>:invoke` grant for Refresh. Studio leases the native team role containing only `gateways.update` to the calling administrator for 120 seconds. Ordinary account/status/tool calls do not renew it. The provisioning service does not receive discovery rights. Refresh uses the caller's own native provider connection and can affect shared native definitions before publication; it is not an atomic rollback mechanism.

Personal OAuth stays native and uses encrypted PostgreSQL storage. Keep the existing encryption keys and approved OAuth metadata, including exact issuer and callback. Shared keys remain in OpenBao and reach their runtime through External Secrets. Required shared-key workloads wait at zero replicas until configured. Publish verifies the intended credential version and exact native membership; Disable retains saved selections.

## Installed catalog and verification

AgentGateway retains `studioSetup`, `catalogHash`, `catalog.json`, `studio.json` and `registrations.json` in `infra-agentgateway/infra-agentgateway-mcp-catalog`. Tooling uses this non-secret catalog for approved credential preparation.

Bootstrap updates `frontend-studio/contextforge-setup` with one ConfigMap PUT. It retains `setup_mode=studio-v1`, `team_id`, `global_role_id`, `team_role_id`, `owner_role_id`, `service_account_email`, `mappings.json`, `ready`, `catalogResourceVersion`, `catalog_hash`, `studio.json` and `publication.json`. Administrator readiness uses `admin_discovery_role_id` and `admin_discovery_ready=true`; a retained role ID alone does not authorize discovery.

Installed entries have empty `tool_names`; their real gateway/server IDs are verified before publication. Failed entries remain visible without an unverified gateway ID. The `publication.json` envelope contains the matching `catalog_hash`, UTC verification-start `checked_at` and integration `{id, state, error_code}` entries. Installation states are `pending-discovery` or `error`, not proof of published user selections. Safe error codes are `verification-failed` and `provider-unavailable`; provider responses and credentials are hidden. Failures are isolated per integration, while core identity failures or a changing catalog prevent publication.

Studio reads the complete projected directory at `/var/run/contextforge-setup` through one atomic `..data` snapshot and overlays its PostgreSQL publication state. `K8S_STUDIO_CONTEXTFORGE_PUBLICATION_STATUS_PATH` remains the locator. Catalog updates do not require a catalog-reloader restart. Only the Studio API mounts the separate scoped OpenBao/Kubernetes identity tokens; native bootstrap retains read/update access to the two named ConfigMaps and no Secret API access.

Configure approved provider egress/SSRF networks and both `contextforge.setup.kubernetesApiEgress` and `frontendStudio.api.mcpSetup.kubernetesApiEgress`, covering the API Service and post-DNAT server destinations. Keep private ingress restrictions even though managed native registrations use public visibility inside ContextForge.

## Removed modes and retained data

Named `operatorDiscovery`, chart-owned publication and `setup.publishOAuthTools` are removed. Selected obsolete modes fail with explicit errors; disabled legacy defaults do not block current configuration. Permanent named-operator grants must be explicitly retired through an approved access change before removing obsolete `operator_*` projection keys. Bootstrap rejects a retained `operator_role_id` or grants attached to its marked legacy roles; it never deletes grants or live data.

An existing `studio-v1` installation keeps its registrations, accounts, tools, personal connections and Studio selections. Do not follow historical clean-cutover instructions that remove registrations or require reconnection. A genuinely older chart-owned installation requires a separately reviewed in-place ownership/selection migration, not deletion or automatic adoption.

## Upgrade maintenance

See [the current upgrade procedure](../../docs/operations/contextforge-upgrade.md) before adoption. The migration Job is a **pre-upgrade** hook, so Deployment `Recreate` does not stop the old application before migrations. Stop callers and Studio publication workers, drain in-flight operations and stop native application Pods before allowing the hook to run. Retain all databases, keys, credentials and stable IDs. Source changes alone do not authorize deployment or prove that a compatible Studio image has been published.

## Independently rerun bootstrap

Bootstrap can retry installation verification without publishing user selections. Use the installed Base revision and the same ordered approved values files as its HelmRelease. Render locally with `helm template`, then use `scripts/contextforge_setup_job.py --name <unique-job-name>` to select only the setup Job and remove hook metadata. Inspect the result before an explicitly authorized `kubectl create`; never apply the entire render or replace the Helm hook Job. This does not run migrations or restart the application. Inspect completion and safe logs, then remove only that completed temporary Job and local rendered files.
