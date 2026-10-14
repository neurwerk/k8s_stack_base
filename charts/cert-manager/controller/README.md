# cert-manager controller metrics

The optional inspection PKI chart's ServiceMonitor selects only the existing
certificate controller's Service in `infra-cert-manager`. The pinned cert-manager
`v1.20.2` Service port
`tcp-prometheus-servicemonitor` targets Pod port `http-metrics`, TCP `9402`.
Scrapes use `/metrics` every 30 seconds with a 10-second timeout. The ingress
NetworkPolicy permits that port only from Pods labeled
`app.kubernetes.io/name: prometheus` and
`operator.prometheus.io/name: monitor-kube-prometheus-st`
in `monitor-kube-prometheus-stack`. It selects only the controller Pods.

The metrics endpoint defaults on through `cert-manager.prometheus.enabled`.
The optional inspection PKI chart owns the controller-only ServiceMonitor, gated
by both inspection and monitoring enablement. Keep the vendor's
`servicemonitor.enabled` and `podmonitor.enabled` off so cert-manager can bootstrap
before monitoring CRDs exist. The optional PKI release waits for the existing
`monitor-kube-prometheus-stack/kube-prometheus-stack` HelmRelease.

This keeps installation order acyclic:

```text
cert-manager controller -> approval policy -> Rook/Ceph
  -> kube-prometheus-stack -> optional inspection PKI monitoring
```

The pinned kube-prometheus-stack renders an empty ServiceMonitor selector and
namespace selector (all namespaces), and a rule selector for
`release: monitor-kube-prometheus-stack` across all namespaces. The monitor and
inspection rules carry that release label. Their optional release dependency
ensures the monitoring CRDs are available before the monitor and rules install.

## Label contract

The vendor Service's `app.kubernetes.io/name: cert-manager` supplies the scrape
`job`. The normal release identity supplies `service: infra-cert-manager-controller`
and target `namespace: infra-cert-manager`; the endpoint label is
`tcp-prometheus-servicemonitor`.

With `honorLabels: false`, exported certificate `namespace` becomes
`exported_namespace`, while the target `namespace` remains `infra-cert-manager`.
The [pinned certificate collector](https://github.com/cert-manager/cert-manager/blob/v1.20.2/internal/collectors/certificate_collector.go)
exports `name`, `namespace`, `issuer_name`, `issuer_kind`, `issuer_group`, and
`condition` (`True`, `False`, `Unknown`) for readiness. Expiry and renewal use
Unix timestamps; uninitialized status is exported as zero. The inspection rules
match these labels and the expected issuer for each configured generation.

After an authorized adoption, verify discovery of the controller target, `up=1`,
and both inspection namespaces under `exported_namespace`. Certificate issuance
and loaded native signer evidence are separate checks; see
[inspection monitoring](../../agentgateway/inspection-pki/README.md#monitoring).
