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
```

Both booleans must be `true` to expose the proxy. Supply one exact lowercase DNS
hostname and an integer port from 1 to 65535. The port must differ from the
configured `web`/`websecure` container and exposed/host ports and the K3s vendor
admin/metrics ports (`8080`, `9100`). The Kubernetes CRD provider must be enabled.

The shared port creates the `forward-proxy` TCP entrypoint, Service port and
host port. An `IngressRouteTCP` in `infra-agentgateway` matches only
`HostSNI(<proxy.hostname>)` and passes TLS through to the fixed same-namespace
Service `infra-agentgateway-forward-proxy` on that same port. The HTTP default
entrypoints are explicitly limited to `web` and `websecure` while enabled.

AgentGateway owns outer TLS termination with `infra-agentgateway-tls`, employee
authentication, destination restrictions and inspection. Traefik receives no
inspection CA or TLS Secret through this feature. The root AgentGateway chart
must admit the `kube-system`/`traefik` NetworkPolicy peer on the shared proxy port.
Enable exposure only after the public inspection trust assets are published and
the AgentGateway hostname certificate and inspected listener are ready.

The embedded values are compatible with K3s `v1.36.3+k3s1`'s vendor chart
`40.1.4+up40.1.0` (Traefik `3.7.8`). Render the vendor chart with the generated
`HelmChartConfig.spec.valuesContent` when checking entrypoint changes; rendering
this configuration chart alone does not validate the resulting Traefik Pod.
