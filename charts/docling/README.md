# Docling

- Disabled and excluded from normal stages; gateway extraction and credential provisioning are still pending.
- Runs upstream Docling Serve on CPU and calls your external Granite-Docling server. No CPU-inference fallback; llama.cpp needs `--special`.
- Set the inference URL, model, private IPv4 CIDRs, port and optional CA in `docling.inference` client values.
- Supply separate existing Secrets through `docling.apiKeySecretRef` and `docling.inference.tokenSecretRef`; never put keys in values.
- Shared `documentAttachments` defaults: 20 MiB/file, 40 MiB/request, 5 files and 200 pages. Override them in client-wide values.
- Uses private HTTPS and temporary storage in namespace `docling`. Reconcile its namespace, optional Reloader package and certificate approval before enabling it.
- Completed results are cleaned periodically; timeouts do not cancel active jobs. LibreChat's stored originals are untouched.
- LibreChat raw delivery is opt-in through `frontendLibrechat.documentAttachments.enabled`, with normal waiting behavior. See the [architecture docs](https://github.com/neurwerk/documentation/blob/main/dev/architecture/docling.md) for setup details and limitations.
