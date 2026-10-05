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
  image: ghcr.io/neurwerk/k8s-stack-tooling:0.7.3@sha256:efa09b2f29d02fbd07fe12774fd7d3db6bf4e02ef403c7be2c18196fbc956d99
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
Base runtime Secret), realm and admin username values, and a **published and
verified** Tooling image supporting `KC_REALM_ROLE_COMPOSITE_OWNERSHIP`.
Base's realm-role Job must also run that image and publish an ownership scope
covering its core `platform-admin` children. An older image silently ignores
the scope and removes foreign addon grants. Coordinate migration of the previous
single addon-application-access-values source before selecting an addon. The
addon must own the HelmRelease, values, and reconciliation ordering; this chart
creates no HelmRelease and changes no client source or selected platform tag.
