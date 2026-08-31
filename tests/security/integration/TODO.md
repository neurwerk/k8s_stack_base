# MCP Security Integration Tests

Implement only after MCP is deployed to a controlled cluster.

- Reject requests that supply `x-auth-user` or `x-user-id` without valid credentials.
- Verify accepted and rejected JWT `azp` values and audiences.
- Verify valid and invalid API-key flows through extAuth.
- Verify MCP discovery is anonymous while `initialize`, `tools/list`, and `tools/call` require authentication.
- Verify MCP requests bypass the LLM extProc parser.

Use `kubectl` through a Python helper that checks the explicitly selected cluster context. Supply live tokens and API keys only through environment variables or CI secrets.
