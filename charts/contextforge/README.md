# ContextForge

The chart runs private ContextForge and a bootstrap Job for installed MCP destinations.
Stop the application before native database migrations and retain its encryption keys.

## Studio MCP Setup

Set the single `mcp.studioSetup.enabled: true` switch consistently in AgentGateway,
ContextForge, Studio API and OpenBao values. Adopt the matching Studio API/Web images
in the same cutover: the new catalog schema is incompatible with Studio `0.16.4`.
Keep `contextforge.setup.enabled`, native trusted-proxy access, Studio ContextForge
connections and onboarding enabled. The setup switch supplies role-based discovery;
do not enable the old named-operator mode.

Charts own installed integrations, stable IDs/URLs, credential policies and checks.
Studio/PostgreSQL owns enabled state, selected tools and publication progress.
Bootstrap creates gateways as the setup administrator and empty native virtual
servers as `contextforge-studio`. Subsequent bootstrap only verifies those servers;
it never replaces their tool membership or enabled state, imports selections,
copies personal connections, or publishes chart-selected tools.

The fixed team owner's role has exactly `admin.user_management`,
`teams.manage_members`, `teams.read`, `gateways.read`, `servers.create`,
`servers.read`, `servers.update` and `tools.read`, with no inheritance. Setup
reconciles this chart-owned permission set only after checking role ownership.
The temporary caller discovery role still contains only `gateways.update`.
Individual provider OAuth stays native and uses the calling administrator's
connection. Shared keys stay in OpenBao and reach their runtime through ESO.

The setup ConfigMap contains installed catalog entries, verified native IDs,
`setup_mode=studio-v1`, role/team IDs and administrator-discovery readiness. Its
`publication.json` envelope holds `catalog_hash`, verification-start `checked_at`
and integration `{id, state, error_code}` entries (`pending-discovery` or `error`).
This verifies installation; Studio's database owns actual publication. Studio
loads all keys from one atomic directory snapshot using the existing
`K8S_STUDIO_CONTEXTFORGE_PUBLICATION_STATUS_PATH` locator. An unavailable
registration has no unverified gateway ID and can be retried by normal bootstrap.

Only the Studio API container mounts `/var/run/mcp-identity`: an OpenBao-audience
token, a separate Kubernetes API-audience token, and the Kubernetes CA. OpenBao
uses the fixed `studio-mcp` role and `/var/run/contextforge/ca.crt` trust bundle.
No separate activation or role environment setting is needed.

### Clean cutover

1. Prepare the exact shared OpenBao paths and scoped Studio/ESO roles with the
   matching Tooling version; seed empty records only when absent.
2. During the approved cutover, remove only the affected old native server/gateway
   IDs and obsolete setup projection. This is a separate operator action, never
   part of bootstrap. Existing MCP selections, keys and personal connections are
   deliberately not imported.
3. Adopt compatible Studio images and the chart set, including the shared switch,
   native provider egress/SSRF ranges, `contextforge.setup.kubernetesApiEgress`, and
   `frontendStudio.api.mcpSetup.kubernetesApiEgress` for the API Service and server
   addresses. Required shared-key workloads wait at zero replicas until configured.
4. Verify native bootstrap, ESO delivery, gateway/controller readiness, and Studio
   setup. Administrators then save keys, discover tools and publish selections in
   Studio. Later bootstrap runs preserve these choices.

## Chart-owned publication (`mcp.studioSetup.enabled: false` only)

The remaining sections describe the older chart-owned workflow. They do not apply
to Studio MCP Setup.

## Role-based administrator discovery

The preferred opt-in flow uses `mcp-admin`, not a specially named login. Enable
`contextforge.setup.adminDiscovery.enabled` and
`frontendStudio.api.contextforge.adminDiscovery.enabled` only after adopting
Studio `0.16.4` or newer with the compatible Base charts.
Both default to false; Studio `0.16.3` and earlier cannot use this mode.

Setup creates/verifies the non-inheriting `contextforge-tool-discovery` native
team role with exactly `gateways.update`, without granting it to a person or
service. It publishes `admin_discovery_role_id` and `admin_discovery_ready=true`
only after verification. On failure or disable, the ID is retained as history,
but the ready key is withheld and cannot authorize discovery.

An `mcp-admin` connects personally and explicitly clicks **Discover tools**.
Studio verifies its own admission role, the administrator role, verified email,
and the integration invocation grants before checking the existing native user.
The existing provisioning service leases the discovery role to that caller for
120 seconds, then the private refresh API uses that caller's own saved provider
connection. Native role scope, grantor and expiry are verified. No separate login,
token copying, shared provider connection or automatic fallback user is introduced.
Ordinary account/status/tool checks do not renew this grant. Keycloak is the
authority for explicit lease renewals; disabled native accounts and revoked
ordinary membership/invocation grants remain blocked. Native management remains
private, because upstream `gateways.update` also admits gateway edits.

Successful discovery still requires the independently rerun setup Job below to
verify and publish the approved catalog, then explicit client route activation.
Keep the published catalog when a discovery connection disconnects; another
authorized administrator can later refresh using their own connection. Native
refresh can change shared records before publication; this is not atomic rollback.

The legacy `operatorDiscovery` mode is mutually exclusive. If it was ever enabled,
explicitly retire its permanent discovery assignment and role through native APIs,
then remove only its obsolete `operator_*` projection keys before enabling this
mode. Setup refuses migration while `operator_role_id` remains; it never silently
deletes old grants or recreates a revoked role. Existing personal connections and
provider registrations are preserved. Installations where it stayed disabled
need no identity migration.

## Legacy named operator discovery

The feature defaults off. Do not enable it until compatible Studio support is
published and separately adopted; this chart change does not change image pins,
client defaults, provider selection or AgentGateway route activation.

Configure `contextforge.setup.operatorDiscovery` and
`frontendStudio.api.contextforge.operatorDiscovery` consistently:

```yaml
operatorDiscovery:
  enabled: false
  operatorEmail: ""
  operatorSubject: ""
  roleName: neurwerk-mcp-discovery
```

When enabled, both identity fields must be explicit. The existing named native
account must be active, email-verified and non-admin, with only the fixed team
member profile and the ordinary exact invocation roles. Studio must compare the
verified Keycloak subject and email to this binding and require `studio-user`
and existing integration invocation permissions. Native accounts do not store
Keycloak subjects; the issuer/subject boundary belongs to Studio.

Setup creates one non-inheriting team role with exactly `gateways.update` and
assigns it only to that member in the fixed team. Its identity marker binds the
email and subject immutably. Dormant, expiring, extra, revoked or missing previous
grants are not repaired. If initial provisioning stops after creating the role
but before confirming its assignment, explicit operator repair is required.
The Studio service principal keeps its existing narrow provisioning permissions;
it does not receive the discovery role.

The operator connects personally through Studio, then explicitly discovers using
their own native identity. Native refresh requires `gateways.update` and uses the
caller's email. That permission also admits native gateway edits, so Studio must
expose only the fixed catalog-checked tools refresh action, never a general API
proxy. Native management remains private. Setup never calls OAuth refresh or
reads/borrows operator tokens; deprecated `setup.publishOAuthTools` is ignored.
Initial non-OAuth registration retains native discovery behavior.

## Verification and non-secret publication

The existing Job verifies ownership, exact approved active tools and exact server
membership before filling an initially empty OAuth virtual server. Pending OAuth
registrations have an enabled safe empty server and a verified gateway, allowing
personal Connect without waiting for discovery. Existing nonempty servers are
never silently rewritten. Native refresh is **not atomic** in the pinned upstream:
failed refresh can affect native records before setup runs. This change does not
implement staged refresh or record preservation; those belong to upstream
#7014/#7021. A last-good Studio projection does not repair native state or certify
that a failed refresh preserved it.

Provider errors are isolated. Core Studio team/role configuration and verified
providers are published even while others are pending or fail. Unverified new
entries are omitted; previous verified entries are retained only when their exact
approved native definition, explicit mappings and Studio checks/origin match.
An operator-profile error withholds the operator binding but does not suppress
ordinary Studio configuration. A native-call budget reserves publication time;
remaining providers report errors rather than preventing all output.
Each provider's complete projection is saved before the next provider begins,
so a later exhausted budget cannot erase an earlier verified result.
Core identity/ownership failures or a catalog changed during setup fail closed.

Published Studio `0.16.1` requires an OAuth entry when personal connections are
enabled. With operator discovery disabled, setup conservatively protects every
selected or previously published OAuth catalog: if no safe OAuth entry survives
verification, it fails **without updating the output ConfigMap at all**. The old
snapshot and its old status/hash/timestamp remain unchanged, so no catalog
reloader restart or falsely current publication occurs. Verified pending OAuth
entries retain their real gateway and enabled empty server for Connect. Publishing
an empty/partial catalog that removes the last OAuth entry requires the explicit
operator-discovery opt-in and separately adopted dynamic-snapshot Studio support;
setup does not infer consumer settings or bypass this guard from unknown values.

The Job updates existing `frontend-studio/contextforge-setup` in one ConfigMap PUT.
Its `studio.json` retains the strict existing schema, including empty `tool_names`
for pending providers. Existing `team_id`, `global_role_id`, `team_role_id`,
`owner_role_id`, `service_account_email`, `mappings.json`, `ready` and
`catalogResourceVersion` keys remain; `ready=true` means core configuration was
published, not that all providers are ready. Mapping records also carry a
non-secret approved-definition hash for safe last-good retention.

Separate `catalog_hash` contains the authoritative approved chart catalog hash
from setup configuration, verified against the source catalog before publication.
It is written in the same ConfigMap snapshot as `studio.json` and
`publication.json`. Studio must require a well-formed exact match between this
sibling key and `publication.json.catalog_hash`, not merely valid hash syntax.
Retained entries must match current approved definitions; the legacy no-write
path leaves the entire old snapshot, including its old hash, untouched.

Verified enabled operator setup adds `operator_email`, `operator_subject` and
`operator_role_id`. A previously published role ID is retained as revocation
history even when the email/subject binding is withheld; that ID alone never
admits Discover. Separate `publication.json` always contains:

```json
{
  "catalog_hash": "generated catalog hash",
  "checked_at": "2026-10-08T12:00:00+00:00",
  "integrations": [
    {"id": "example", "state": "pending-discovery", "error_code": null}
  ]
}
```

States are `pending-discovery`, `published` and `error`.
Only error states carry one of these safe codes:

| Code | Meaning |
| --- | --- |
| `verification-failed` | Approved state could not be verified, including denied native calls or exhausted native budget. |
| `provider-unavailable` | A transport or unexpected provider-processing failure; details suppressed. |

No provider responses, credentials or account details enter status. `checked_at`
is the UTC **verification-start time**, captured before any native verification,
not publication-completion time. Studio must require a timestamp newer than
Discover completion before showing freshly verified publication; a Discover
concurrent with an already-running setup therefore needs a subsequent setup run.
A preserved old projection still reports `error` for the failed current verification.

With operator discovery enabled, the Studio chart mounts the complete ConfigMap
directory at `/var/run/contextforge-setup` without `subPath`, and sets
`K8S_STUDIO_CONTEXTFORGE_PUBLICATION_STATUS_PATH` to its `publication.json`.
Studio reads sibling catalog/status/binding keys through one resolved `..data`
snapshot. Updates do not require API restart; the catalog reloader is disabled
only for this opt-in mode. The existing published Studio keeps its original
environment/reloader contract when the feature is off. No Studio Kubernetes
client, token mount or RBAC grant is added.

## Independently rerun setup

Discover does not trigger publication. After discovery, an authorized operator
runs setup separately; Studio may poll read-only completion for about two minutes
and otherwise show that publication still needs a setup run.

Use the **installed Base revision** and the same ordered, approved non-secret
values files as its HelmRelease. The mounted setup-code ConfigMap must already
contain that revision. From the Base checkout, render locally, select only setup,
then explicitly create a fresh Job (commands below are operator instructions,
not a deployment performed by this change):

```bash
umask 077
mise exec -- helm template contextforge charts/contextforge --namespace contextforge \
  --values /path/to/approved-client-values.yaml \
  --values /path/to/approved-contextforge-defaults.yaml \
  --values /path/to/approved-contextforge-product-values.yaml \
  > /tmp/opencode/contextforge-render.yaml
mise exec -- uv run --frozen python scripts/contextforge_setup_job.py \
  --name contextforge-setup-rerun-unique \
  < /tmp/opencode/contextforge-render.yaml > /tmp/opencode/contextforge-setup-rerun.yaml
kubectl create -f /tmp/opencode/contextforge-setup-rerun.yaml
```

Choose a new Job name each time; do not replace the Helm hook Job. The selector
removes hook metadata and controller identity, and emits only the setup Job with
its existing security, deadline, mounts, network policy labels and service account.
Do not apply the full render: no Helm upgrade, migration, application restart or
per-provider Job is involved. Inspect that Job's completion/log summary and
publication status, then delete only that completed fresh Job and your temporary
rendered files when finished. Setup's Kubernetes RBAC remains limited to the two
named ConfigMaps, without Secret API access.
