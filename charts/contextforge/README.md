# ContextForge

The optional chart runs private ContextForge and a setup Job that registers the selected MCP catalog and supplies Studio's configuration.
Keep credentials in OpenBao; stop the application before database migrations and preserve the database with its encryption keys.

## Named operator discovery (source preparation)

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
Core identity/ownership failures or a catalog changed during setup fail closed.

The Job updates existing `frontend-studio/contextforge-setup` in one ConfigMap PUT.
Its `studio.json` retains the strict existing schema, including empty `tool_names`
for pending providers. Existing `team_id`, `global_role_id`, `team_role_id`,
`owner_role_id`, `service_account_email`, `mappings.json`, `ready` and
`catalogResourceVersion` keys remain; `ready=true` means core configuration was
published, not that all providers are ready. Mapping records also carry a
non-secret approved-definition hash for safe last-good retention.

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
is UTC publication time; Studio must require a newer timestamp than Discover
before showing freshly verified publication. A preserved old projection still
reports `error` for the failed current verification.

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
