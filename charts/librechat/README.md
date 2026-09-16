# Optional Speech

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
