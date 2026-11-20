# Docling

Docling is disabled by default. Select it in shared client values with `docling.enabled: true` and adopt the compatible charts, images and setup prerequisites from the release manifest.

## Deployment

| `docling.mode` | Processing | Credential delivery |
| --- | --- | --- |
| `internal` (default) | Official CPU Serve in Kubernetes; layout, OCR and tables run together. | `releases/docling/secret-sync/internal` |
| `external` | A complete Serve API operated outside this chart, such as the official GPU runtime. No local Serve workload is rendered. | `releases/docling/secret-sync/external` |

External mode uses `docling.external.url`, `cidrs` and `port`. HTTPS verification is enabled; `caConfigMap` can supply an additional trusted CA. A private HTTP endpoint requires explicit `allowHttp: true` and matching destination restrictions.

Serve authenticates `X-Api-Key` using a nonempty `DOCLING_SERVE_API_KEY`. Preserve the existing Docling key and the gateway's copy during migration. Internal Serve reads its namespace-local `apiKeySecretRef`; the external operator supplies the matching key to Serve. Never put credentials in chart values.

## Conversion and limits

Gateway conversion uses the standard pipeline with server-owned default layout, OCR and table presets. The internal image includes its model artifacts; the GPU runtime uses verified cached models. Separate model servers, private VLM presets and `native-first` PDF conversion are retired.

Shared `documentAttachments` defaults are 20 MiB per file, 40 MiB per request, five files and **two cumulative PDF pages**. Configure these in shared client values; a GPU client can explicitly select 250 pages. extProc checks the whole batch before upload. Serve additionally enforces its per-document limit; configure an external server to match the client's page allowance.

Attachment delivery remains a per-model choice. Text extraction passes through the configured text-PII policy. Normalized image forwarding uses the selected image policy. Optional PDF `render-images` requires a concrete local vision model with PII disabled and unchecked normalized images.

## Optional HTTPS API

External mode can expose a client-selected hostname:

```yaml
docling:
  enabled: true
  mode: external
  external:
    url: http://10.0.0.20:8011
    allowHttp: true
    cidrs: [10.0.0.20/32]
    port: 8011
  ingress:
    enabled: true
    host: docling.example.test
```

This route requires a private HTTP IPv4 backend with a matching `/32` destination. Traefik terminates HTTPS and forwards the caller's `X-Api-Key` unchanged. Configure DNS separately; cert-manager issues the hostname's certificate through the selected issuer.

Only authenticated conversion, asynchronous conversion, polling and result routes are exposed. Python clients can read the key from an environment variable and send it as `X-Api-Key`; no Studio key is involved. Direct Serve calls perform conversion without the LLM gateway's PII processing or aggregate request limits. The managed GPU runtime defaults to 20 MiB per file, three sources per request and 250 pages per document.

For adoption and resource removal, follow the [migration notes](../../release/migrations/v0.3.42.md).
