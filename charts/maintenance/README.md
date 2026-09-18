# On-Demand Maintenance

This optional package is excluded from the default platform composition. Compose
`releases/namespaces/maintenance` and `releases/maintenance` separately. Disabled
defaults render only the permanent ClusterIP Service and namespace-wide ingress
isolation/egress denial. There is no Deployment, IngressRoute, Job, credential,
certificate, or activation state managed by Helm.

## Values Contract

`maintenance.enabled` defaults to `false`. When enabled, `maintenance.image` must
be a verified published `ghcr.io/neurwerk/k8s-stack-tooling:X.Y.Z@sha256:<64 lowercase hex digits>` image
providing `maintenance-server`. Each version component is a nonnegative integer
without leading zeros; prerelease/build suffixes and other repositories or mirrors
are unsupported. The default pins published, verified Tooling `0.7.0` for
`linux/amd64`, from source revision `7d2f9475db90ecf6d8804a688ef4b7809fa8af26`.
See the [Tooling release](https://github.com/neurwerk/k8s_stack_tooling/releases/tag/v0.7.0)
and [successful publication run](https://github.com/neurwerk/k8s_stack_tooling/actions/runs/35313889657).
Explicit adoption and runtime acceptance remain required.

`maintenance.products.<product>.enabled` is an approval scope, not runtime
activation. All four default to `false`; enabled maintenance requires at least
one selection. No arbitrary hostname list or additional product is accepted.

| Product | Canonical Hostname Values |
| --- | --- |
| `studio` | `frontendStudio.studio.hostname` |
| `dify` | `frontendDify.hostname` |
| `librechat` | `frontendLibrechat.hostname`, `frontendLibrechat.adminPanel.hostname` |
| `langfuse` | `monitorLangfuseWrapper.hostname` |

Both LibreChat hostnames are required when selected. Selected hosts must be
nonempty, exact lowercase DNS hostnames with an alphabetic first character in the
final label (not IP addresses), unique across all selected products,
and not placeholders. Keycloak, model and storage scopes are not supported;
collisions with their supplied canonical hostnames are rejected.

The HelmRelease reads namespace-local `client-values` followed by
`maintenance-product-values` (both `values.yaml`, optional for safe disabled
composition). The client must project canonical facts rather than duplicate
domain literals. Project Keycloak's same canonical
`authKeycloak.realmDisplayName`, `authKeycloak.branding.logoConfigMapName`, and
`authKeycloak.branding.logoFormat` values as well as the named logo ConfigMap
into `maintenance`. Format accepts `png` or `svg`, defaults to `png`, and selects
key `company-logo.<format>`. Branding is required when maintenance is enabled.

Resource sizing comes from `maintenance.resources.requests` and `.limits`;
both require `cpu` and `memory`. Defaults are requests `10m`/`64Mi` and limits
`100m`/`128Mi`. The runtime uses UID/GID 1000, no API token, no egress, a read-only
root filesystem and logo mount, and a bounded writable `/tmp`.

Keep prepared maintenance stages outside the client's selected root resource
inventory until adoption is approved. The access planner
does not accept suspended selected stages.

## Operator Contract

Enabled rendering creates `ConfigMap/maintenance-runtime` in `maintenance`.
Its `data["contract.json"]` is JSON with exactly these top-level fields:

```text
version: 1
namespace: maintenance
serviceName: maintenance
servicePort: 8080
deployment: full apps/v1 Deployment named maintenance, replicas 1
routes: global plus only the approved product IngressRoutes
```

All runtime objects and the Pod template use these fixed labels:

```yaml
app.kubernetes.io/name: maintenance
app.kubernetes.io/instance: maintenance
app.kubernetes.io/part-of: maintenance
maintenance.neurwerk.com/managed-by: operator
```

Runtime templates contain no Helm ownership labels/annotations or owner
references. The operator owns deployed copies, readiness checks, activation,
deactivation and cleanup. Helm never automatically creates them or falls back
to normal applications. Disabling the chart gate does not remove operator copies;
the operator must deactivate before removing their static dependencies.

Each IngressRoute is named `maintenance-<scope>`. Its
`maintenance.neurwerk.com/hosts` annotation is a JSON hostname list. Routes use
exact `Host` rules for all paths, `websecure`, and `tls: {}` to reuse certificates
already loaded into Traefik's default TLS store by existing product Gateways.
No TLS Secret is copied, and no middleware or backend health check is attached.
The global union has priority `2000000000`; product overlays use `1900000000`.
All route backends are the same-namespace `maintenance:8080` Service.

Base explicitly enables both Traefik providers and CRD `allowEmptyServices` by
default. Keeping the overlay router when the Service has no ready endpoints
prevents accidental fallback to an application. Ordinary product routes remain
unchanged, and this release has no normal application/server dependency.

## Runtime Acceptance Pending

The published server must run `maintenance-server` on port 8080 using
`MAINTENANCE_COMPANY_NAME`, `MAINTENANCE_LOGO_PATH`, and
`MAINTENANCE_RETRY_AFTER=300`. `/_maintenance/healthz` must return 200 for the
readiness/liveness probes. Normal application paths must return 503 with
`X-Platform-Maintenance: true` and `Retry-After: 300`, independently of application
or identity-server health. Offline chart tests validate the actual embedded JSON
and Deployment schema, not the published server's live HTTP behavior. TLS reuse,
empty-backend fail-closed behavior, NetworkPolicy enforcement and server behavior
still require separately authorized runtime acceptance before rollout.
