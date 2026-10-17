# AI endpoints

## API connection

Use the full base URL in the client. Send platform identity in `X-Neurwerk-Authorization: Bearer <token-or-key>`, separately from provider credentials.

| API base URL | Service | Provider credential modes |
| --- | --- | --- |
| `https://ai-api.example.com/openai/codex/v1` | Codex Responses | Caller-supplied OAuth session, managed by the client |
| `https://ai-api.example.com/openai/api/v1` | OpenAI API | Configured company key or caller-supplied API key |
| `https://ai-api.example.com/openrouter/api/v1` | OpenRouter API | Configured company key or caller-supplied API key |
| `https://ai-api.example.com/deepseek/api/v1` | DeepSeek API | Configured company key or caller-supplied API key |
| `https://ai-api.example.com/local/api/v1` | Local models | No provider credential |

`https://ai-api.example.com/github/copilot/v1` is reserved for future work, not implemented.

Company requests omit provider credentials; caller-supplied credentials use the provider's approved header. Available modes depend on the binding configuration. Supported company/local OIDC clients can use `Authorization: Bearer <token>`; company use requires the provider company-use permission. Codex login is handled by the client: users sign in there rather than copying OAuth tokens. The gateway API endpoint does not redirect to a browser login.

## Inspected proxy connection

Configure proxy URL `https://ai-proxy.example.com:443`. Keep the client's native destination URL:

| Service | Native destination URL | Provider credential modes |
| --- | --- | --- |
| Codex | `https://chatgpt.com/backend-api/codex/responses` | Caller-supplied OAuth session, managed by the client |
| OpenAI API | `https://api.openai.com/v1` | Configured company key or caller-supplied API key |
| OpenRouter API | `https://openrouter.ai/api/v1` | Configured company key or caller-supplied API key |
| DeepSeek API | `https://api.deepseek.com/v1` | Configured company key or caller-supplied API key |

Only enabled inspected bindings and admitted operations are available. Authenticate with Proxy Basic over TLS, using the platform API key as password, and install the published inspection CA after verifying its fingerprint. Provider login remains client-managed. Model invocation permissions apply in both connection modes.

## Codex and rollout

Codex uses profile `openai-codex-responses-v1`, provider `OpenAI`, `credentialSources: [caller_supplied]`, and upstream `https://chatgpt.com/backend-api/codex`. Enable `api`, `inspect`, or both. API clients append `/responses`; proxy clients retain the native path and provider `Authorization` and optional `chatgpt-account-id` headers. The profile reuses Responses processing for HTTP JSON/SSE with request-local restoration. Encrypted reasoning replay is supported; `previous_response_id` server-side continuation, WebSocket and website traffic are unsupported.

Codex billing classification is unknown unless explicitly configured. Set `billingMode: included` only for a Codex binding whose requests are covered by an included plan. Its native accounting uses provider `codex`; supply zero rates for the selected models in that provider's client model catalog to record $0. Subscription classification follows trusted final dispatch and the configured billing mode. OAuth or caller-supplied credentials alone do not imply free usage, and ordinary OpenAI API prices remain separate.

Content recording follows each model's `contentTracingEnabled` setting. Usage and cost availability describe native reported accounting, independently of final delivery status; missing usage or pricing remains unknown. Native parsers can default individual missing counters, so a reported total is not proof that every original provider counter was present.

Set each binding's `apiPath` to the table's URL path without `/v1`, retaining existing binding names and canonical model IDs. Omitting `apiPath` retains `/bindings/<id>/v1`. A custom prefix replaces that binding's API mount and is included as optional trusted `request_context.api_path_prefix` in API mode only. Deploy compatible extProc support before selecting custom prefixes or the Codex profile. Update consumers together; the catalog and LibreChat use the configured prefix. A new Codex binding needs distinct canonical model IDs when its upstream model also appears on the existing OpenAI binding.
