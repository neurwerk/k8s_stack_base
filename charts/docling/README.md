# Docling (Disabled Source Addition)

This optional package is excluded from the platform inventory and all normal
stages. It is not an enabled attachment feature. Install only in namespace
`docling`, using release name `docling`, after the integration prerequisites below
are complete. No image build, model download, persistent volume or GPU is needed
by this chart.

## Configuration Ownership

`releases/shared/document-attachments.yaml` defines canonical `documentAttachments`
limits: `fileBytes: 20971520`, `totalBytes: 41943040`, `count: 5`, `pages: 200`.
Chart-safe defaults match these values. Client-wide overrides belong in canonical
`client-values`, projected into Docling and LibreChat namespaces (and future
extProc configuration). Do not duplicate these overrides in product values.
LibreChat converts byte limits to numeric MiB only when generating its fileConfig.
The native service handles **one file per conversion**; request-wide file count,
total decoded bytes and aggregate page admission belong to future extProc.

Client product values supply `docling.inference.url`, `model`, `cidrs`, `port`,
optional `caConfigMap`, and existing `apiKeySecretRef`/`inference.tokenSecretRef`
name/key references. The URL must be HTTPS ending in `/v1/chat/completions`, with
no userinfo, query or fragment. Only RFC1918 IPv4 inference CIDRs are supported;
operators must verify that DNS resolves into those reviewed CIDRs. NetworkPolicy
is an IP/port boundary, not an HTTP path or hostname allowlist. TLS verification
is never disabled. A custom CA ConfigMap contains a public `ca.crt` bundle and
sets `REQUESTS_CA_BUNDLE`; it replaces Requests' roots for this process.

Provision both distinct credentials through a future approved OpenBao/ESO catalog
before enablement. This package creates no Secret values, SecretStore or
ExternalSecret. Set the same canonical `docling.enabled` flag in the certificate
approval and extProc value projections. After the Docling namespace exists,
compose `releases/docling/reloader` to extend the existing Reloader's watched
namespace and scoped RBAC lists through its optional values ConfigMap. The package
adds only `docling` to the defaults and is not selected by normal stages. Reconcile
Reloader, approval policy and the internal issuer before Docling; annotations alone
do not make an unwatched namespace reload. The chart requests a rotating RSA-2048, 90-day server
certificate for the fixed service DNS identity; reloader watches its Secret,
credentials and optional upstream CA. extProc's shipped 0.7.1 image is unchanged;
the gated egress rule adds no unsupported application environment variables.

## Runtime Contract

The upstream CPU image is pinned to
`ghcr.io/docling-project/docling-serve-cpu:v1.33.0@sha256:546cf392145a0a578f23e4250663a37a5fb727fe6b57fd163e301584ad8bc18c`.
The registry-verified index contains amd64 and arm64; amd64 manifest is
`sha256:e034edf2914d56503b6c968891e8c8cffb0b749708c42740052b8aab383e1bfa`.
Source is `27fa2aa9638e449d7fcd4364ffcde8d9a47bc4eb`, with docling-slim 2.127.0,
docling-core 2.96.1 and docling-jobkit 3.6.0. No first-party fork is introduced.
Only registry metadata was inspected, not image layers or a running container.

`/opt/app-root/bin/python /config/bootstrap.py` reads the mounted, non-secret
JSON (unprefixed upstream settings), validates nonempty/no-CRLF credentials, and
injects only `Authorization: Bearer ...` into the `default` preset through
`DOCLING_SERVE_CUSTOM_VLM_PRESETS` **before any Docling import**. Upstream does not
interpolate environment variables in JSON. The separate caller key stays in
`DOCLING_SERVE_API_KEY` via secretKeyRef. Neither credential is written to disk.
Python logging is disabled through CRITICAL both before and after importing
`create_app`; Uvicorn uses `log_config=None`, no access log, one worker and native
TLS. Startup errors are fixed text, never configuration or exception bodies.

The full Granite-Docling-258M preset replaces the literal built-in `default` with
the API engine, one inference call at a time, scale 2, DocTags and 8192 output
tokens. Model loading at boot, plugins, custom caller VLM config, UI, management,
telemetry and enrichments are disabled/restricted. Offline HF/Transformers prevent
downloads. No CPU fallback is implemented. Format-specific CPU parsing is not a
fallback: Office/text inputs use their native parsing paths rather than GPU OCR.
The external llama.cpp server must preserve DocTags special tokens (`--special`)
and load the matching model and vision projector; an ordinary stripped-text
completion is not equivalent to DocTags. The chart does not configure that server.

The trusted future extProc consumer MUST send only one uploaded file with
`pipeline=vlm`, `vlm_pipeline_preset=default`, `to_formats=[json]`,
`target_type=inbody`, `image_export_mode=placeholder`, no callbacks, no image
exports and all enrichments disabled. It must never forward caller options,
URLs, models or credentials. Validate the status/error envelope and
`document.json_content`, including empty/error output, before using a result.
No fallback to the raw attachment is permitted after failed extraction.

## Native Limits And Gaps

- Native max-file and page limits do not prevent all multipart/body buffering.
  Uvicorn limits concurrent connections to 8 and incomplete HTTP events to 16 KiB;
  these are not body-size or durable queue limits. The pipeline queue buffer is 1,
  not a global admission queue cap. Future extProc must bound uploads, queued work,
  output bytes/text and deadlines before enabling extraction.
- Defaults request a 300-second document budget and 90-second inference calls,
  with a 360-second synchronous wait. Native sync timeout does not cancel jobs;
  VLM timeout is checked after a batch and Requests retries can exceed the budget.
  These are not hard termination guarantees. Do not retry blindly.
- One local worker, one cached converter, 1 GiB scratch and 256 MiB `/tmp` are
  disposable per-Pod emptyDirs, with explicit memory/ephemeral-storage limits.
  Neither `/opt/app-root` nor baked model artifacts are overmounted. No PVC exists.
- Fetched results are single-use with 60-second removal delay. Unfetched completed
  local results have no native TTL. A five-minute, nonconcurrent CronJob calls
  authenticated `GET /v1/clear/results?older_then=600`, verifying the server CA.
  It imports only Python stdlib, mounts only `ca.crt` (not the server private key),
  and can reach only DNS and Docling. Failures emit fixed text. Cleanup is periodic,
  not an exact retention deadline; running/stuck jobs are not cancelled by it.
- `/livez` and `/readyz` are native unauthenticated HTTPS probes of local lifecycle
  and the task loop, not checks of GPU availability or extraction quality.
- The stock server retains legacy local-pipeline/source-HTTP paths and other
  authenticated APIs. Its policy settings are NOT a global upstream allowlist.
  The private service, separate credentials, network isolation and trusted fixed
  extProc request are the boundary. No browser or general caller gets this key.
  Native docs/health paths remain reachable by allowed peers even with UI off.
- Suppression covers Python logging, not a guarantee about arbitrary native
  library stderr. The operator must keep inference-server content logging off.

## LibreChat Opt-In

`frontendLibrechat.documentAttachments.enabled` defaults to false in the shared
configuration chart. When true, `fileConfig.endpoints.AgentGateway` prefers
provider delivery with the PDF/DOCX/XLSX/PPTX/plain-text/Markdown/CSV MIME allowlist
and shared file-count/byte caps. This uses the existing upstream delivery schema;
it does not enable any model attachment mode, RAG, UI extension or custom waiting
behavior. Local text delivery remains an existing user alternative. Stored
originals and GUI deletion behavior are unchanged. The app already mounts and
reloads this ConfigMap, so no app-chart change or image update is needed.
