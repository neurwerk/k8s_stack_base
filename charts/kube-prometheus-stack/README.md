# kube-prometheus-stack wrapper

## K3s metrics profile

`values-k3s.yaml` is an opt-in profile for the
[K3s shared metrics registry](https://docs.k3s.io/reference/metrics).
Use it when the API-server scrape covers the embedded control-plane metrics
also exposed through the kubelet scrape. The ordinary chart defaults do not
enable this profile.

The profile retains the upstream kubelet storage-bucket filter and drops
`apiserver_*`, `etcd_*`, `kine_*`, and `scheduler_*` from kubelet `/metrics`.
The API-server scrape is the canonical source for those families.

### Select through Flux

Add this patch under `spec` of the Flux **infrastructure Kustomization** that
reconciles `releases/infrastructure` from the Base source:

```yaml
patches:
  - target:
      group: helm.toolkit.fluxcd.io
      version: v2
      kind: HelmRelease
      name: kube-prometheus-stack
      namespace: monitor-kube-prometheus-stack
    patch: |-
      apiVersion: helm.toolkit.fluxcd.io/v2
      kind: HelmRelease
      metadata:
        name: kube-prometheus-stack
        namespace: monitor-kube-prometheus-stack
      spec:
        chart:
          spec:
            valuesFiles:
              - ./charts/kube-prometheus-stack/values.yaml
              - ./charts/kube-prometheus-stack/values-k3s.yaml
```

[Flux values-file paths](https://fluxcd.io/flux/components/source/helmcharts/#values-files)
are relative to the Base source artifact. Include `values.yaml` first to retain
the wrapper defaults. This patch belongs on the infrastructure Flux resource,
not the cluster root Kustomize file or the bootstrap self-sync resource.

The selected Base revision must contain the profile before an existing
client-side `metricRelabelings` override is removed. HelmRelease `valuesFrom`
and inline values take precedence over the packaged profile. Arrays replace
rather than append: a later `metricRelabelings` list must contain every filter
the consumer needs. To return to the ordinary chart filters, remove the profile
selection and any overriding list.

For local rendering from the Base repository root:

```sh
mise exec -- helm template metrics charts/kube-prometheus-stack \
  -f charts/kube-prometheus-stack/values-k3s.yaml \
  --show-only charts/kube-prometheus-stack/templates/exporters/kubelet/servicemonitor.yaml
```

### Coverage and rollout

Full-family filtering removes both duplicate series and extra histogram buckets
absent from the API-server scrape. The API SLI boundaries `1`, `5`, `30`, and
`+Inf` remain available there. Queries selecting the kubelet copies must use the
canonical source; review external dashboards and ad-hoc consumers before opting
in. One-hour and thirty-day API availability records temporarily mix the old
duplicated counts with the new single-source counts.

Prometheus Operator's config-reloader applies this ServiceMonitor change by hot
reload. Compare `scrape_samples_post_metric_relabeling`, the rate of
`prometheus_tsdb_head_samples_appended_total`, and `prometheus_tsdb_head_series`
after adoption. Measure storage write bytes separately; a sample reduction is
not a measured disk-write saving.
