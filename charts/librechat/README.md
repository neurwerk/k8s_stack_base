# LibreChat Configuration

## Optional Attachments

`frontendLibrechat.documentAttachments.imagesEnabled` requires document uploads
and attachment policy version 3, 4, or exact string `"4.1"`. Version 3 allows
JPEG, PNG and HEIC and does not allow WebP. Versions 4 and 4.1 additionally allow
`image/webp`; all versions retain `imageOutputType: png`. Upload configuration
does not grant any model attachment mode or image-forwarding permission.

## Optional Memory

The `shared` chart exposes `frontendLibrechat.memory` in client LibreChat values.
It defaults to `enabled: false` and renders `memory.disabled: true`. Enabling it
makes per-user saved memories and their controls available; it does not reset any
user's saved off choice. Users otherwise default to on in the pinned application.

Automatic updates also require `agent.enabled: true` and an explicit `agent.model`
from the effective model catalog. Requests use the existing `AgentGateway`
endpoint and the user's credentials and model permissions, with no new Secret.
For example, with a configured model named `local/example`:

```yaml
frontendLibrechat:
  memory:
    enabled: true
    tokenLimit: 2000
    maxInputTokens: 4000
    messageWindowSize: 5
    agent:
      enabled: true
      model: local/example
```

Clients can override the limits and `agent.instructions`. Default instructions
save clearly stated, lasting preferences automatically, update corrections, and
honor forget requests. The stored-memory token limit, recent-chat token limit,
and recent message count must be positive integers. Automatic updates default to
off independently of manual memory. No inline composer memory tools are enabled.

`personalize: true` and `interface.memories: true` keep the upstream USER/ADMIN
memory controls available without changing the existing Agents/Marketplace role
policy. Users can switch off **Settings > Data controls > Reference saved memories**
and add, edit, or delete entries in **Memories**. Switching off stops future memory
use and automatic updates, not deletion of saved entries or previous chat content.
There is no supported per-user default-off setting in the pinned application.

Memories are stored in the existing LibreChat database. Automatic processing adds
model calls and uses the selected route's tracing and PII policy. A local memory
model does not prevent saved notes being sent with later cloud-model chats.

## Optional Speech

This configuration uses existing LibreChat speech support. No application patch,
upstream contribution, custom image, or image/source pin change is required.
Only configured server STT endpoints are restricted to local addresses; existing
browser recognition remains outside these chart controls and may use a cloud
service. Do not describe this as enforcement of local-only browser recognition.

Both `app` and `shared` consume the same `frontendLibrechat.speech.stt` and `.tts`
values. Each defaults to `enabled: false`, `provider: openai-compatible`, empty
`url` and `model`, and `auth.enabled: false`. TTS also defaults to `voices: []` and
`allowExternal: false`. Disabled directions create no provider configuration,
speech egress, environment variables or ExternalSecrets. When both directions
are disabled, no speech configuration is rendered.

Enabled directions require a nonempty model and a full HTTP(S) API URL with a
canonical RFC1918 IPv4 address and explicit port from 1 to 65535. For example,
`http://10.20.30.40:8000/v1/audio/transcriptions` is a synthetic STT URL, not a
default. DNS, public, loopback, link-local and noncanonical IP addresses are not
accepted for STT. URLs cannot contain credentials, query strings, fragments,
substitutions or encoded characters. Paths use letters, digits, `/`, `.`, `_`,
`~` and `-`. The URL alone determines the exact `host:port` SSRF allowance and
`/32` TCP NetworkPolicy rule; there is no separate address or port override.

TTS also requires a nonempty list of voice names. Explicit `tts.allowExternal:
true` additionally accepts a lowercase DNS HTTPS URL on port 443 (omitted or
explicit), with a full API path. This approves sending response text to that
external service. External TTS gets no private-address SSRF exemption and uses
the app's existing public TCP 443 egress, which is not a DNS allowlist. Local
speech traffic is server-to-server and does not pass through AgentGateway or PII
Engine. TLS verification remains enabled for HTTPS endpoints.

When a direction is enabled with `auth.enabled: true`, the app chart creates
`frontend-librechat-stt-secret` or `frontend-librechat-tts-secret` through
`frontend-librechat-openbao-secret-store`. Each reads only its `sttApiKey` or
`ttsApiKey` property from OpenBao KV `frontend-librechat/external`. Required
`secretKeyRef` entries supply `LIBRECHAT_STT_API_KEY` or `LIBRECHAT_TTS_API_KEY`,
and Reloader watches the selected Secrets. The ConfigMap contains only the
matching environment placeholder; no-auth providers get `apiKey: ""`. No broad
runtime-secret fields are required.

Speech UI engines default to `external` for enabled directions. These are user
defaults, not locks. Conversation mode remains controlled by the user. Browser
recognition behavior is unchanged. Use the same speech values for both charts;
changing only one release breaks the configuration, credential and egress contract.

With STT enabled, `frontendLibrechat.speech.stt` also accepts
`autoTranscribeAudio` (boolean, default `false`), `decibelValue` (negative number,
default `-45`), and `autoSendText` (integer seconds, default `-1` to disable).
For example, `autoTranscribeAudio: true` and `autoSendText: 3` stop recording after
the application's silence interval, transcribe the audio, then send the text
three seconds after transcription succeeds. Existing browser preferences take
precedence; users with saved settings can change them in the Speech settings.
