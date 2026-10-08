# PII Engine NER layers

CPU pattern and custom recognizers remain independent of NER selection. Policy,
entity actions and anonymization stay in the Engine. `monitorPiiEngine.ner: null`
(the default, also equivalent to omitting it) retains legacy automatic offline
baseline / verified transformer-bundle selection.

Explicit `ner` requires a compatible Engine **>=0.13.0**. This is a planned
compatibility minimum, not proof of publication. The chart still pins the verified
**0.12.0 CPU image**; leave `ner: null` until a published, verified compatible image
is adopted separately. The examples below describe that future configuration and
deliberately do not supply a pretend image digest.

## Local NER

Select bundled small spaCy models (3.8.0), without transformer-cache mounts:

```yaml
monitorPiiEngine:
  ner:
    mode: local
    languageModels: {en: english-spacy, de: german-spacy}
    models:
      english-spacy: {profile: spacy-en-sm-v1}
      german-spacy: {profile: spacy-de-sm-v1}
```

`spacy-nl-sm-v1` is also available for Dutch. The Engine owns the profile catalog,
supported-language checks, recipes and model identities; Helm does not copy model
pins or label/tokenizer tables.

## CPU rules without NER

```yaml
monitorPiiEngine:
  ner:
    mode: disabled
```

Omit `models` and `languageModels` (or use empty maps). Do not set remote capacity,
egress or tokenizers. Disabling NER does **not** disable CPU recognizers or policy.

## One multilingual remote model

```yaml
monitorPiiEngine:
  ner:
    mode: remote
    languageModels: {de: multilingual-pii, en: multilingual-pii}
    models:
      multilingual-pii:
        profile: gliner-multilingual-pii-v1
        endpoint: https://ner.example.com/extract
        modelName: ner-multilingual
        inferenceThreshold: 0.45
        # Optional approved namespace-local Secret; never put its value here.
        apiKeySecretRef: {name: ner-credential, key: bearer}
    egress: [{cidr: "192.0.2.10/32", port: 443}]
```

Assigning English and German to the **same model ID** means one multilingual call
per chunk, not one per language. The GLiNER profile defaults to upstream
`urchade/gliner_multi_pii-v1`; `modelName` defaults to `ner-multilingual`.
Declare the installed server's `inferenceThreshold`; it must not exceed the active
policy threshold. Optional `upstream` and `revision` pass through to Engine
identity validation. Verify the actual server assignment and any required
revision separately; aliases are not evidence of weights or detection quality.

## Mixed remote adapters

```yaml
monitorPiiEngine:
  ner:
    mode: remote
    languageModels: {en: english-pii, de: multilingual-pii}
    models:
      english-pii:
        profile: kserve-en-openpii-v1
        endpoint: https://ner.example.com/v1/models/ner-english:predict
        modelName: ner-english
        tokenizerPath: /remote-tokenizers/english
      multilingual-pii:
        profile: gliner-multilingual-pii-v1
        endpoint: https://ner.example.com/extract
        inferenceThreshold: 0.45
    tokenizerClaimName: ner-tokenizers
    egress: [{cidr: "192.0.2.10/32", port: 443}]
```

`kserve-de-superclinical-v1` supports German. Every KServe model requires an
explicit `modelName`, `tokenizerPath`, and an existing PVC containing the
profile's checksum-pinned tokenizer-only files. Helm mounts the PVC read-only at
`/remote-tokenizers`; remote weights are never mounted. Mixed remote profiles
are allowed, but local and remote profiles cannot share a mode.

Model IDs and profile IDs do not determine adapter type or mounts. The Engine
owns profile kind and mode validation, including added profiles with arbitrary
IDs. Helm mounts tokenizer resources whenever a model declares `tokenizerPath`,
requiring `modelName` and an existing `tokenizerClaimName` regardless of its
profile ID. A tokenizer claim without any model using it is rejected. Adding an
Engine profile does not require Helm wiring changes.

## Limits and trust

Active modes require 1–3 models. IDs match `^[a-z][a-z0-9-]{0,63}$`; supported
language keys are `en`, `de`, `nl`. Every reference must exist, and every model
must be referenced. Each model requires `profile`; unknown fields fail validation.
Final detailed profile, language, endpoint protocol and identity validation belongs
to the Engine and fails closed at startup.

Remote-only capacity defaults and allowed ranges:

| Field | Default | Range |
| --- | --- | --- |
| `callTimeout` | 10 seconds | >0 through 60 |
| `maxCalls` | 2048 | 1–10000 |
| `maxResponseBytes` | 2097152 | 1024–8388608 |
| `maxConcurrentCalls` | 1 | 1–16 |

Set overrides under `ner.capacity`. These bounds are per Engine process; more
replicas do not coordinate admission to a shared server.

Remote mode requires exact destination host CIDRs (`/32` IPv4 or `/128` IPv6)
and TCP ports in `ner.egress`. Replace example addresses with reviewed server
addresses. HTTPS uses normal certificate verification. Plain HTTP requires
`allowPrivateHttp: true` on that model plus operator-approved private network
isolation. URL credentials, query strings and fragments are forbidden.

The ConfigMap contains `ner.yaml`; `PII_ENGINE_NER_CONFIG` points to it.
Chart-only `egress`, `tokenizerClaimName` and `apiKeySecretRef` are removed from
that file. Secret keys are mounted read-only at
`/var/run/pii-engine/remote/<model-id>/api-key` and passed as `apiKeyFile`, never
as Secret values. All explicit modes use the CPU image without accelerator
allocation and omit transformer-cache mounts. Existing model-sync jobs and cache
resources remain intact for legacy workloads or a later switch back.

## Migration and inherited defaults

Keep existing clients on `ner: null` to preserve their current behavior.
When migrating, replace meaningful old selectors with the explicit `ner` block:
non-local `analyzerBackend`, nonempty `remote.models`, or nondefault
`policy.pii.ner` cannot coexist with canonical selection.

Helm merges defaults before rendering and cannot tell whether default values were
inherited or explicitly restated. Therefore canonical mode ignores inherited
`analyzerBackend: local`, `remote.models: []`, default bundle pins and the default
`policy.pii.ner` block (including explicitly restated identical defaults). It
removes legacy `pii.ner` from the emitted policy and emits no legacy backend,
remote-capacity or bundle environment selectors. Bundle values have no effect in
canonical mode. Set them only for a deliberate return to legacy operation.
