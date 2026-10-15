# Traefik edge configuration

This chart configures the K3s-managed Traefik through `HelmChartConfig`.
It retains the singleton `Recreate` rollout, `ClusterIP` Service and direct
HTTP/HTTPS host ports.

## Inspected forward proxy

The proxy is disabled by default. Both the Traefik and root AgentGateway
HelmReleases consume `client-values`; declare this shared configuration once in
the client's `config/client.yaml`, not in separate product overrides:

```yaml
aiGateway:
  enabled: false
  proxy:
    enabled: false
    hostname: ""
    port: 8443
    publicPort: 443
```

Both booleans must be `true` to expose the proxy. Supply an exact lowercase DNS
hostname distinct from `infraAgentgatewayWrapper.hostname` (the API hostname).
`publicPort` must match `traefik.ports.websecure.exposedPort`.
The Kubernetes CRD provider must be enabled.

An `IngressRouteTCP` on the existing `websecure` entrypoint matches only
`HostSNI(<proxy.hostname>)` and passes TLS through to the same-namespace Service
`infra-agentgateway-forward-proxy` on private `proxy.port`. Other hostnames retain
their HTTP routers. This adds no node port or TLS termination at the edge.

AgentGateway owns its separate public proxy certificate, outer TLS termination,
employee authentication, destination restrictions and inspection. Traefik receives
no inspection CA or TLS Secret through this feature. The root AgentGateway chart
must admit the `kube-system`/`traefik` NetworkPolicy peer on the private proxy port.
Enable exposure only after the public inspection trust assets are published and
the AgentGateway hostname certificate and inspected listener are ready.

The embedded values are compatible with K3s `v1.36.3+k3s1`'s vendor chart
`40.1.4+up40.1.0` (Traefik `3.7.8`). Render the vendor chart with the generated
`HelmChartConfig.spec.valuesContent` when checking entrypoint changes; rendering
this configuration chart alone does not validate the resulting Traefik Pod.
