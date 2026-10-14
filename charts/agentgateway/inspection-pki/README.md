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
existing controller and approval-policy prerequisites. Directory presence alone
does not select it. The client owns separate Flux stages and their `dependsOn`
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
   has been consumed. Only then may the client PKI stage reconcile.
2. **PKI:** select `releases/agentgateway/inspection-pki`. Wait for HelmRelease
   `infra-agentgateway/agentgateway-inspection-pki` to be Ready; its health
   expressions require current-generation Ready Issuers and Certificates and
   Synced Bundles. Verify the root DER fingerprints from the public exports.
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
