# Per-addon Keycloak access

One selected addon owns one `HelmRelease` of this chart in `auth-keycloak`.
Base does not include this chart in its default release stage. The addon supplies
its own values and must depend on `keycloak-realm-roles` before running its Job.
Use a unique, lowercase product key without hyphens; declared roles must start
with `<key>-` and groups with `/access/neurwerk-<key>-`. Product keys must be
unique across selected addons. The chart cannot inspect other releases: review
their ownership lists and coordinate disjoint role and group identities.

Example non-secret values for the existing Forgejo identities:

```yaml
authKeycloak:
  realm: example
k8sTools:
  image: ghcr.io/neurwerk/k8s-stack-tooling:0.7.4@sha256:6f6a72a2b16f6bd93771c60190f5e0dba552790c283ea99ad4005cbf94e6894e
addonAccess:
  enabled: true
  name: forgejo
  realmRoles: [forgejo-user, forgejo-admin]
  realmRoleComposites:
    forgejo-admin: [forgejo-user]
  accessGroups:
    /access/neurwerk-forgejo-users:
      realmRoles: [forgejo-user]
    /access/neurwerk-forgejo-admins:
      realmRoles: [forgejo-admin]
  platformAdminRole: forgejo-admin
  platformAdminGrant: true
```

Set `platformAdminGrant: false` to remove this addon's direct grant only.
If a child is removed from an addon composite, keep it in `ownedRoles` until
the stale grant is removed; an empty `ownedRoles` defaults to `realmRoles`.
This does not revoke other addon grants, a user's direct memberships, already
issued tokens, or native application sessions. Removing the release does not
automatically remove previously created Keycloak roles, groups, or composite
grants; reconcile with `platformAdminGrant: false` first and review cleanup.
No AgentGateway client roles or model/MCP grants are provided by this chart.

The addon HelmRelease needs namespace-local `auth-keycloak-secret` (the existing
Base runtime Secret), realm and admin username values. When enabled, the chart
requires the exact verified Tooling `0.7.4` tag and digest shown above; image
overrides to older or unverified builds fail rendering.
Base's realm-role Job now uses the same verified image and scopes its own
`platform-admin` children. It rejects the old `addonApplicationAccess` values;
the default Keycloak releases no longer read the shared
`addon-application-access-values` ConfigMap. Remove those legacy inputs in a
reviewed client cutover before selecting an addon. The addon must own the
HelmRelease, values, and reconciliation ordering; this chart creates no
HelmRelease and changes no client source or selected platform tag.

Base Active Directory no longer reads the legacy shared add-on group values.
Addon group mappings require a separate reviewed allowlist and ordering handoff: Base realm
roles Ready -> selected addon access Ready -> Active Directory Ready. Do not
configure addon mappings or assume an addon group exists until that handoff is
implemented and selected. Removing an addon later also requires reviewing any
directory mappings and existing access before cleanup.
