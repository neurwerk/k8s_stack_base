# Docling

- Disabled by default and excluded from normal stages; requires the compatible gateway and PII images before client activation.
- Prefer `docling.inference.mode: internal-standard` or `private-vlm`. Legacy `cpu` and `remote` are exact aliases; the shipped default remains `remote`. There is no automatic failover.
- Internal-standard mode uses built-in OCR, layout and table models in the same Pod, without an external server or runtime downloads.
- Private-vlm mode needs the server URL, model, private IPv4 CIDRs, port and token reference. An optional CA applies only there; llama.cpp needs `--special` for Granite-Docling.
- `docling.inference.maxOutputTokens` defaults to 8192 and must fit beside the prompt and image tokens inside the private model's total context window. Lower it for servers whose total context is 8192.
- HTTPS is required by default. Set `docling.inference.allowHttp: true` to explicitly allow a private HTTP endpoint; set `port` to its matching port (80 when omitted from an HTTP URL). RFC1918 egress restrictions and the TLS-protected Docling service remain unchanged.
- Both modes need `docling.apiKeySecretRef`; only remote needs `docling.inference.tokenSecretRef`. Never put keys in values.
- CPU clients use `releases/docling/secret-sync/internal`; remote clients use `releases/docling/secret-sync`. Follow the package's pinned CLI prerequisite.
- Shared `documentAttachments` defaults: 20 MiB/file, 40 MiB/request, 5 files and 200 pages. Override them in client-wide values; maxima are 40 MiB/file and total, 20 files and 1000 pages.
- Only the trusted gateway may submit fixed conversion options. See the [architecture docs](https://github.com/neurwerk/documentation/blob/main/dev/architecture/docling.md) for setup, cleanup and limits.
- Mode aliases do not enable images. Verified extProc `0.10.0` and PII Engine `0.10.0-cpu` are pinned in Base; extProc retains `cpu` / `remote` environment values. Deploy compatible services before separately enabling version-3 CPU/HEIC image processing; see the root README.

## Private Image Preset

Private-vlm/remote adds the administrator-owned `images` preset with `scale: 1.0`
and top-level `max_size: null`. In pinned Docling `2.127.0` these are
`VlmConvertOptions` fields, not `model_spec` fields. Both presets receive the
same private endpoint and token. PDF/document requests keep `default` at scale 2
with its previous model settings; `default` is always registered by Jobkit,
even though the explicit allowlist is now `[images]`. Internal-standard/cpu
has neither remote preset, credentials nor inference egress. Request-supplied
custom VLM configuration remains disabled.

The new extProc consumer must select `vlm_pipeline_preset=images` only for
normalized standalone image input. Normalization must strip DPI metadata:
the upstream reader interprets DPI as page geometry even at scale 1. Install
this preset before activating that consumer's image path; do not change old
consumers' `default` requests. Larger unscaled images can use more upstream
memory than capped images, so existing normalization and request limits remain
necessary. Adding the preset changes the configuration checksum and will restart
an enabled Docling Pod during a separately authorized rollout.

An upstream-backed synthetic check is available:

```bash
mise exec -- uv run --script tests/validation/docling_image_reader.py
```

It fetches exact Docling revision `014e8e357b24aa9d5113317fa454df8a70de9aeb`
(`v2.127.0`) and executes its image reader, page-image sizing, VLM preparation
and API PNG payload code with HTTP intercepted. Two nonuniform RGB images,
including one wider than 2048 pixels, retain their size and RGB pixels;
scale-2 and DPI-tagged controls demonstrate that the test detects resizing.
This is an opt-in source-backed contract check, not a new live release gate.
It uses Pillow `11.3.0` in an isolated test environment and substitutes service
and model plumbing; it does not start the pinned container or prove its full
integration. The VLM server may still resize internally, and no live inference,
OCR accuracy or detector quality guarantee follows from this check.
