# Architecture

## Packages and owners

| Source | Responsibility |
| --- | --- |
| [`charts/`](../../charts) | Helm templates, reusable defaults, schemas where supplied, and locked chart dependencies. Application behavior belongs to the component repository. |
| [`releases/`](../../releases) | Kustomize packages containing Flux HelmReleases and supporting resources. Releases using local charts select them from the client's `k8s-stack` GitRepository. |
| [`releases/shared/`](../../releases/shared) | Platform hostname templates, resource defaults, OIDC settings, and shared gateway policies. |
| [`release/config.yaml`](../../release/config.yaml) | Release intent: package selection, prerequisites, compatibility, trust, and provenance. |
| [`release/manifest.yaml`](../../release/manifest.yaml) | Generated release inventory, including charts, images, and toolchain evidence. |

Client repositories own Flux source selection, environment values, enabled products, and stage dependencies. Add-ons supply their own charts and product-specific identity and database provisioning. Base supplies shared services and extension points; it does not activate an add-on merely because a shared dependency exists. Tooling supplies commands invoked by setup operators and chart Jobs.

## Composition and ordering

The [namespace package](../../releases/namespaces/kustomization.yaml) creates shared namespaces. [Infrastructure](../../releases/infrastructure/kustomization.yaml) composes storage, secrets, certificates, ingress, monitoring, PII services, and shared application prerequisites. [Applications](../../releases/applications/kustomization.yaml) composes identity, gateway, ContextForge, Langfuse, and Studio releases. These Kustomize resource lists are composition, not a readiness sequence: clients provide Flux stage dependencies, and HelmReleases declare their own `spec.dependsOn` edges.

Studio illustrates the boundary. Its [application package](../../releases/studio/kustomization.yaml) generates stable-name ConfigMaps from shared defaults and includes API and Web HelmReleases. The [API release](../../releases/studio/api.yaml) loads those defaults before `client-values` and `studio-product-values`, and waits for its shared resources, ingress, PII Engine, OpenSearch, AgentGateway, database provisioning, and OIDC configuration. Its separate [secret-sync package](../../releases/studio/secret-sync/kustomization.yaml) must run after OpenBao records and SecretStores are ready and before the consuming releases. It delivers database credentials to the application and database-provisioning namespaces without modifying the shared database StatefulSet Secret.

Optional packages have their own prerequisites in the release manifest. For example, inspected proxy access needs certificate approval, inspection PKI, public-root publication, and Studio trust readiness before listener activation. Neither inclusion in the manifest nor a Helm dependency grants application or model access.

## Request and identity boundaries

Traefik exposes configured routes; AgentGateway selects bindings and checks caller permissions. The binding catalog supplies model API routes, inspected-proxy routes, and Studio's gateway view. extProc connects gateway processing to PII Engine; PII Engine owns detection and policy decisions. Studio reads gateway configuration rather than owning routing. See [AI endpoints](../endpoints.md) for URL and credential configuration.

Keycloak owns platform identities and roles; API Key Bridge issues and validates platform keys. Provider credentials are separate from platform identity. OpenBao stores most application secrets, External Secrets delivers scoped fields, and certificate controllers own workload certificates. ContextForge owns personal MCP provider connections; its [setup guide](../../charts/contextforge/README.md) describes the publication and credential boundaries.

The shared [architecture guide](https://github.com/neurwerk/docs/blob/main/dev/architecture/README.md) describes cross-repository request flows. The [deployment conventions](https://github.com/neurwerk/docs/blob/main/dev/conventions/deployment.md) define settings ownership, readiness gates, and resource ownership.
