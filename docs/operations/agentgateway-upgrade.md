# Agentgateway 1.7 upgrade

Use this procedure when adopting a release whose [manifest](../../release/manifest.yaml) selects these components. Follow the shared [upgrade procedure](https://github.com/neurwerk/docs/blob/main/dev/operations/upgrades.md) for publication, adoption, backups, and reconciliation approval.

| Component | Target |
| --- | --- |
| Agentgateway controller, proxy, and CRDs | 1.7.0 |
| kube-prometheus-stack and its CRDs | 92.21.2 |
| Prometheus Operator and config reloader | 0.94.2 |
| Prometheus / Alertmanager | 3.16.0 / 0.34.2 |
| Grafana, when enabled | 13.3.0 |

## Maintenance sequence

1. Confirm Kubernetes 1.25 or newer and review client overrides against the [monitoring upgrade notes](https://github.com/prometheus-community/helm-charts/blob/kube-prometheus-stack-92.21.2/charts/kube-prometheus-stack/UPGRADE.md). Enable the selected maintenance scope and pause affected Flux reconciliation while preparing the target. Confirm backups of the gateway request-log database and monitoring data.
2. Update Prometheus Operator CRDs from the locked kube-prometheus-stack 92.21.2 archive using server-side apply and their existing owner. The wrapper has `crds.enabled: false`; upgrading its HelmRelease alone does not upgrade CRDs. Reconcile monitoring and wait for Operator, Prometheus, and Alertmanager readiness. These schemas support the gateway chart's `scrapeNativeHistograms` field.
3. Update the separately owned `agentgateway-crds` chart to 1.7.0, then reconcile the agentgateway HelmRelease. It also waits for monitoring readiness. Keep the existing Kubernetes resource names, Secrets, and persistent volumes.
4. Allow the gateway to finish its request-log database migration before reopening traffic. Although the upstream notes call it a standalone migration, this platform configures the same PostgreSQL request-log backend. Startup adds analytics columns and backfills existing rows; the duration depends on retained data.
5. Check the applied Flux revisions, resource conditions, warning events, and bounded logs. Verify retained gateway history, new request logs, healthy gateway scrape targets, alert delivery, and enabled Grafana dashboards. Confirm ordinary and streamed model requests and session grouping, then disable maintenance. Existing operator-confirmed feature checks can be reused.

Use one coordinated maintenance window. Recovery follows the manifest's forward-fix policy; reverting Git or Helm does not reverse database or CRD changes.

## Override compatibility

- Monitoring now uses Secret-based ServiceMonitor authorization and a `kube-root-ca.crt` ConfigMap reference. The chart creates a Prometheus service-account token Secret. Migrate old `bearerTokenFile`, `caSecret`, and component-specific TLS overrides as described upstream; custom external scrapers need their own authorization settings.
- Monitoring workloads default to Linux nodes. Preserve the selected [K3s metrics profile](../../charts/kube-prometheus-stack/README.md#k3s-metrics-profile) when applicable.
- Grafana's distroless image has a read-only root filesystem and a chart-managed `/tmp` mount. Replace `GF_*__FILE` and `GF_INSTALL_PLUGINS` overrides as described in the [Grafana chart guide](https://github.com/grafana-community/helm-charts/blob/grafana-14.4.0/charts/grafana/README.md#to-1300).
- Review [agentgateway's behavior changes](https://github.com/agentgateway/agentgateway/releases/tag/v1.7.0), especially repeated-header CEL values, prompt guards inspecting tool outputs, dynamic-backend address restrictions, and earlier LLM buffer enforcement. Keep the platform's explicit buffer limits and credential checks.

## Langfuse sessions

The gateway detects native agent sessions from headers such as `x-claude-code-session-id`, Codex session headers with a Codex user agent, and `x-session-id`. Native detection accepts at least eight letters, digits, hyphens, or underscores.

`langfuse.session.id` hashes the verified principal and detected session together. Repeated requests from that principal share a session; another principal using the same supplied ID gets a different group. Missing or invalid sessions, or missing trusted principal metadata, fall back to the trusted request ID. If that fallback is also unavailable, the attribute is omitted. Every grouped request retains its own `neurwerk.request_id`.

Scoping happens in the tracing expression after authentication. Keep native `standardAttributes.session` detection separate: it runs before trusted identity metadata is available. Native request logs retain the upstream session value. MCP transport sessions and content-tracing permissions have separate meanings.

See the [native regression instructions](../../tests/live/agentgateway/README.md#native-session-grouping) for local OTLP and streaming checks.
