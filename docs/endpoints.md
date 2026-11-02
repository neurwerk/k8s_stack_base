# AI endpoints

## API connection

Copy the endpoint URL from Studio. Default model and MCP endpoints use `Authorization: Bearer <Neurwerk key or eligible Keycloak access token>`; override and Codex endpoints use `Authorization-Neurwerk` for identity and the provider's native header for provider credentials.

| API base URL | Service | Provider credentials |
| --- | --- | --- |
| `https://ai-api.example.com/openai/codex/v1` | Codex Responses | Override OAuth session, managed by the client |
| `https://ai-api.example.com/override/openai/api/v1` | OpenAI API | Caller-supplied API key |
| `https://ai-api.example.com/openrouter/api/v1` | OpenRouter API | Global credential |
| `https://ai-api.example.com/deepseek/api/v1` | DeepSeek API | Global credential |
| `https://ai-api.example.com/local/api/v1` | Local models | No provider credential |

These examples assume matching `routes.api.path` settings. Ordinary override URLs prepend `/override` to the binding path; Codex keeps its dedicated URL. Global access requires the provider global-use permission. Override discovery needs only Neurwerk identity; invocation also requires provider credentials and never falls back to global access. Codex manages its own login and token refresh.

## Inspected proxy connection

Configure proxy URL `https://ai-proxy.example.com:443`. Keep the client's native destination URL:

| Service | Native destination URL | Provider credentials |
| --- | --- | --- |
| Codex | `https://chatgpt.com/backend-api/codex/responses` | Override OAuth session, managed by the client |
| OpenAI API | `https://api.openai.com/v1` | Caller-supplied API key |
| OpenRouter API | `https://openrouter.ai/api/v1` | Caller-supplied API key |
| DeepSeek API | `https://api.deepseek.com/v1` | Caller-supplied API key |

Only enabled inspected bindings and admitted operations are available. Authenticate with Proxy Basic over TLS, using the platform API key as password, and install the published inspection CA after verifying its fingerprint. Provider login remains client-managed. Model invocation permissions apply in both connection modes.

## Codex and rollout

Codex uses profile `openai-codex-responses-v1`, provider `OpenAI`, `credentialSources: [override]`, and upstream `https://chatgpt.com/backend-api/codex`. Enable `api`, `inspect`, or both. API clients append `/responses`; proxy clients retain the native path and provider `Authorization` and optional `chatgpt-account-id` headers. The profile reuses Responses processing for HTTP JSON/SSE with request-local restoration. Encrypted reasoning replay is supported; `previous_response_id` server-side continuation, WebSocket and website traffic are unsupported.

Codex billing classification is unknown unless explicitly configured. Set `billingMode: included` only for a Codex binding whose requests are covered by an included plan. Its native accounting uses provider `codex`; supply zero rates for the selected models in that provider's client model catalog to record $0. Subscription classification follows trusted final dispatch and the configured billing mode. OAuth or override credentials alone do not imply free usage, and ordinary OpenAI API prices remain separate.

Content recording follows each model's `contentTracingEnabled` setting. Usage and cost availability describe native reported accounting, independently of final delivery status; missing usage or pricing remains unknown. Native parsers can default individual missing counters, so a reported total is not proof that every original provider counter was present.

Set `routes.api.path` to the binding path without `/v1` or the generated `/override` prefix; it defaults to `/bindings/<id>`. The [binding template](../charts/agentgateway/templates/_ai-bindings.tpl) validates paths. Codex bindings require distinct canonical model IDs.
