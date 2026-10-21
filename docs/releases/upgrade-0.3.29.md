# Upgrade to 0.3.29

Use this checklist from `v0.3.28`; earlier stable sources must also complete every crossed release's instructions. Downgrade is unsupported; recovery is forward-fix. The [manifest](../../release/manifest.yaml) declares no alpha promotion revisions: deployed alpha changes are not published stable releases, and alpha promotion needs its exact reconciled revision reviewed and declared first.

## Before the maintenance window

1. Verify the signed Base tag and independent backups of affected databases, application files, OpenBao, credentials and CA material. Downtime is expected; no data reset is required by this upgrade.
2. Prepare all client values and compatible consumers together: Studio `0.20.3`, extProc `0.19.17`, bridge `0.10.4` (including MCP DELETE authentication) and Tooling `0.12.0`. Standalone `stack-setup` remains `0.4.3` at the manifest's exact revision; preserve credentials and do not re-bootstrap.
3. Replace `aiGateway.bindings` with `self-hosted-api-providers`, `external-api-providers` and `external-subscription-providers`; move `api`/`inspect`/`apiPath` into `routes.api.enabled`/`routes.inspected.enabled`/`routes.api.path`. Remove `aiGateway.localModelDiscovery`; API model discovery now follows eligible providers. Rename root `mcp` to `mcp-servers` in every client/product values source. Preserve IDs, permissions, model selection and protection settings; old fields are rejected even when empty.
4. If LibreChat is selected, use the reviewed `0.1.6` add-on revision requiring exactly Base `0.3.29`, and require its publication before stable adoption. `0.1.5` introduced immutable Valkey claim preservation but still has old MCP URLs. Validate the exact Base/add-on pair; neither source version nor an alpha deployment proves stable publication.

Limits remain: 256 aggregate provider/server entries, 32 models/provider, 256 models total, 200 selected MCP servers, 256 public endpoints, 62 inspected providers and 1 MiB catalog JSON.

## During the maintenance window

1. Enable approved maintenance, hold the owning Flux Kustomizations **and** affected HelmReleases, and stop affected consumers using the client's reviewed procedure. Do not allow old charts to reconcile new values, or new consumers to start against the old catalog.
2. If shared MCP delivery is enabled, migrate its metadata producer **before** resuming the gateway. Set `CONTEXT` explicitly. This changes only the ExternalSecret's output template, not the keys in OpenBao; run with Bash `set -euo pipefail`:

```bash
kubectl --context "$CONTEXT" -n infra-agentgateway get externalsecret mcp-runtime-values -o json |
python3 -c '
import json, sys
obj = json.load(sys.stdin)
value = obj["spec"]["target"]["template"]["data"]["values.yaml"]
if value.startswith("mcp:\n"):
    value = "mcp-servers:\n" + value[len("mcp:\n"):]
elif not value.startswith("mcp-servers:\n"):
    sys.exit("STOP: unexpected MCP metadata template")
print(json.dumps([
    {"op": "test", "path": "/metadata/resourceVersion", "value": obj["metadata"]["resourceVersion"]},
    {"op": "replace", "path": "/spec/target/template/data/values.yaml", "value": value}]))
' | kubectl --context "$CONTEXT" -n infra-agentgateway patch externalsecret mcp-runtime-values --type=json --patch-file=/dev/stdin
kubectl --context "$CONTEXT" -n infra-agentgateway annotate externalsecret mcp-runtime-values force-sync="$(date +%s)" --overwrite
```

3. Wait for External Secrets to refresh, then verify the generated metadata without displaying it. **Stop if this fails**; do not delete the Secret or replace its version/configured records. Skip these two metadata steps only when shared MCP delivery is not selected.

```bash
kubectl --context "$CONTEXT" -n infra-agentgateway get secret mcp-runtime-values -o json |
python3 -c 'import base64,json,sys; data=base64.b64decode(json.load(sys.stdin)["data"]["values.yaml"]); sys.exit(0 if data.startswith(b"mcp-servers:\n") else "STOP: MCP metadata has not migrated")'
```

4. Select the signed Base release and matching add-on commit, apply the prepared grouped values, then resume prerequisites before gateway/catalog and application consumers according to client dependencies. Verify shared credential versions are unchanged. Change external MCP clients from `/bindings/mcp-<server-id>/mcp` to `/mcp/<server-id>`; no aliases or trailing-slash routes remain. Binding IDs and invocation permissions stay unchanged.
5. Preserve LibreChat Valkey's installed StatefulSet and PVC. The new chart uses a server-connected lookup to retain the complete immutable claim template after checking Helm ownership; an offline render cannot verify that upgrade. Keep size/class unchanged. If ownership or storage validation fails, stop rather than deleting resources or forcing replacement. RAG/code interpreter remain excluded; their storage is outside this fix.
6. Check exact Flux revisions, Kustomizations, HelmReleases, warning events, bounded logs and retained data. Verify login, model/MCP access, shared-key delivery and Valkey readiness, accepting applicable operator-confirmed feature results. Remove maintenance only when dependencies and applications are healthy.

## If something fails

Keep affected consumers stopped, preserve persistent state and credentials, and apply a reviewed forward fix. Do not move tags backward, revert only one coordinated consumer, delete StatefulSets/PVCs, or reset databases. Any component-native restore or destructive action needs a separate approved procedure.
