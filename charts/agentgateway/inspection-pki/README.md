# Inspection PKI

This optional package uses the existing cert-manager, approver-policy and
trust-manager controllers. `agentgateway.inspection.enabled: false` renders no
resources. The root chain is separate from the internal workload CA. Existing
AgentGateway controller cluster-wide Secret reads remain an existing limitation;
this package grants it no additional access.

`agentgateway.inspection.rootIds` is an ordered list of 1–16 unique DNS labels,
each at most 16 characters. For each generation, Base creates:

- a 10-year ECDSA-256 root in `infra-cert-manager`, key rotation `Never`;
- a dedicated ClusterIssuer using that namespace-local Secret;
- a 90-day ECDSA-256 intermediate in `infra-agentgateway`, key rotation `Always`,
  renewed 30 days before expiry;
- a trust-manager Bundle exporting only the root's `tls.crt` to the public
  `infra-cert-manager/agentgateway-inspection-root-<id>-config-map`, key `ca.crt`.

The native Gateway references only
`infra-agentgateway/agentgateway-inspection-signer-<id>-secrets` using its standard
Kubernetes TLS Secret reference. No root key is exported to the publisher or
Studio. Gateway hostname TLS remains independent.

## Client staging

`release/config.yaml` declares this package as optional/included and lists its
existing controller, approval-policy and kube-prometheus-stack prerequisites.
Directory presence alone does not select it. The client owns separate Flux stages and their `dependsOn`
and health checks; it must keep the inspected forward listener disabled through
the first four stages:

```text
current-generation approval profiles
  -> PKI Ready and public root exports
  -> complete initial public snapshot
  -> Studio required-version readiness
  -> inspected forward listener
```

1. **Approval:** materialize the same `agentgateway.inspection.enabled` and
    `rootIds` in approval-policy and PKI inputs. With the existing controllers and
   namespaces Ready, wait for every `cert-manager-agentgateway-inspection-root-<id>`
   and `cert-manager-agentgateway-inspection-signer-<id>` CertificateRequestPolicy
   to have `Ready=True` with `observedGeneration == metadata.generation`. An old
   Ready approval HelmRelease does not prove a newly changed values ConfigMap
    has been consumed. The independent client PKI-values stage creates
    `infra-agentgateway/agentgateway-inspection-pki-product-values`, key
    `values.yaml`, and health-checks both root and signer approval policies for
    every configured generation. The PKI HelmRelease reads `client-values` first
    and this dedicated product ConfigMap second. Keep `enabled` and `rootIds`
    canonical in the shared client configuration; the dedicated ConfigMap can
    set `agentgateway.inspection.monitoring.enabled: true`. Only then may the
    client PKI stage reconcile, depending on infrastructure and this independent
    values stage. Neither infrastructure nor these values depend on PKI or the
    forward-listener stage.
2. **PKI:** select `releases/agentgateway/inspection-pki`. Wait for HelmRelease
   `infra-agentgateway/agentgateway-inspection-pki` to be Ready; its health
   expressions require current-generation Ready Issuers and Certificates and
   Synced Bundles. The release also waits for
   `monitor-kube-prometheus-stack/kube-prometheus-stack`, providing monitoring
   CRDs before the optional ServiceMonitor and rules install. Verify the root DER
   fingerprints from the public exports.
3. **Initial snapshot:** after the trust package's verified Tooling image is
   adopted and its optional eligibility is included, select
   `releases/agentgateway/inspection-trust` with the reviewed fingerprints and
   operation `initialize`. Wait for its publication Job and HelmRelease to
   succeed, creating a complete `frontend-studio/agentgateway-inspection-trust-config-map`
   with `snapshots.json` and a valid current pointer. Record `required_version`
   from the Job's JSON result. See [publication](../inspection-trust/README.md).
4. **Studio floor:** set `frontendStudio.api.gateway.trust.enabled`,
   `configMapName: agentgateway-inspection-trust-config-map`, and
   `requiredVersion` to that digest. Wait for the API Deployment's current
   generation and every serving replica to become Ready using
   `/api/gateway/trust/bundles/<requiredVersion>.pem`. Its read-only full-directory
   ConfigMap mount must omit `subPath`; startup/liveness stay on `/api/version`.
5. **Forward listener:** only after Studio convergence enable the inspected
   forward listener with the selected native signer TLS Secret reference. Check
   its current readiness and loaded chain through the forward-proxy path first.
   User-browser checks follow the forward-proxy checks.

## Rollover

Routine intermediate renewal does not change root trust; verify loaded native
signer material and drainage separately.

For root rollover, add a new generation alongside the old, publish overlapping
roots, allow the workstation update window, switch the native signer reference,
verify the loaded chain and drained old tunnels, and explicitly retire old current
trust. Retained public snapshots are independent of signer Secret renewal.

Root `rotationPolicy: Never` preserves the key but does not disable certificate
renewal. The pinned cert-manager has no `renewal.policy: Disabled`; perform an
explicit root rollover before `status.renewalTime` (one year before expiry).
Monitor expiry and check intermediate validity against the original trusted root;
the CA issuer does not enforce that bound. The runtime's documented long-lived
leaves rely on full chain validation against the root, never direct signer trust.

## Monitoring

`agentgateway.inspection.monitoring.enabled` defaults to `false`. Opt in through
the dedicated PKI product values after selecting the optional package. Both it
and `agentgateway.inspection.enabled` must be true to render the ServiceMonitor
and PrometheusRule. This package owns
`infra-cert-manager/cert-manager-controller-service-monitor`, targeting only the
existing `infra-cert-manager-controller` Service. Cert-manager exposes metrics
during bootstrap; this optional release enables scraping after the existing
monitoring stack is Ready. See its
[metrics and label contract](../../cert-manager/controller/README.md).

The rules enumerate the exact root and signer Certificates for each `rootIds`
entry, including namespace and issuer. Unconfigured generations and unrelated
certificate authorities do not produce alerts. Retained downloadable trust
snapshots do not create expected Certificate series.

| Alert | Condition | Hold / severity |
| --- | --- | --- |
| `InspectionCertificateSourceUnavailable` | No successful controller scrape, including an absent target | 10m / critical |
| `InspectionCertificateMetricsMissing` | Source is up but an expected Certificate lacks readiness or positive renewal/expiry timestamps | 10m / critical |
| `InspectionCertificateNotReady` | Ready is False or Unknown | 10m / critical |
| `InspectionCertificateRenewalOverdue` | More than 1h past renewalTime, even while Ready stays True | 10m / critical |
| `InspectionCertificateExpiringSoon` | Signer has less than 14 days left; root has less than 365 days left; includes expired Certificates | 10m / critical |
| `InspectionRootRolloverDue` | Root is within 90 days of renewalTime, or past it | 1h / warning |

The 10-year root's one-year renewal window and the 90-day signer's 30-day renewal
window are unchanged. Root rollover warning starts 90 days **before** that
one-year window. It is a planning signal: add a new generation and publish trust
overlap before switching signers. Root renewal can advance the exporter timestamps
and clear an alert; it does not prove that retained public snapshots or installed
workstation trust changed. Keep the explicit rollover procedure above.

Certificate alerts carry `namespace`, `certificate`, `root_id`, and
`certificate_role` (`root` or `signer`); the source alert carries
`namespace: infra-cert-manager`. Missing-source and source-up/missing-series
alerts distinguish a broken scrape from incomplete certificate telemetry.
Warnings are visible in the existing Alertmanager/Grafana; delivery follows the
platform's existing critical/warning routing.

After an authorized adoption, verify both namespaces' expected series and rule
discovery. Resolve issuance/approval faults without manually approving requests.
Confirm the native loaded signer and old-tunnel drainage through the forward
proxy separately; these cert-manager rules cannot establish either. User-browser
checks follow forward-proxy verification.

Offline fault-case tests use `promtool` when present on PATH (or an existing
binary selected by `PROMTOOL`):

```sh
mise exec -- uv run --frozen python -m unittest discover -s tests/charts -p 'test_inspection_monitoring.py' -v
```

They evaluate the rendered rules with synthetic late-renewal, absent/stale-series,
source-down, expiry, and root-rollover data. An unavailable `promtool` is reported
as a test skip, not rule-execution evidence.
