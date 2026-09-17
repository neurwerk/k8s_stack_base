# Docling

- Disabled by default and excluded from normal stages; gateway extraction integration remains pending.
- Select `docling.inference.mode: cpu` or `remote` (the default). There is no automatic failover.
- CPU mode uses built-in OCR, layout and table models in the same Pod, without an external server or runtime downloads.
- Remote mode needs the server URL, model, private IPv4 CIDRs, port and token reference. An optional CA applies only there; llama.cpp needs `--special` for Granite-Docling.
- Both modes need `docling.apiKeySecretRef`; only remote needs `docling.inference.tokenSecretRef`. Never put keys in values.
- CPU clients use `releases/docling/secret-sync/internal`; remote clients use `releases/docling/secret-sync`. Follow the package's pinned CLI prerequisite.
- Shared `documentAttachments` defaults: 20 MiB/file, 40 MiB/request, 5 files and 200 pages. Override them in client-wide values.
- Only the trusted gateway may submit fixed conversion options. See the [architecture docs](https://github.com/neurwerk/documentation/blob/main/dev/architecture/docling.md) for setup, cleanup and limits.
