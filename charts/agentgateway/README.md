# AgentGateway MCP presets

Enable the built-in Context7, Brave and GitHub definitions through `mcp.catalog.presets`; use `mcp.catalog.custom` for additional servers.
For GitHub, [create an organization-owned App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app) and provide its client ID, Studio callback URL and OpenBao-managed client secret.

Each preset or custom entry accepts `checks: [{name: Check connection, tool: get_me, arguments: {}, display: {label: Connected account, field: login}}]`; configure only approved read-only tools and non-secret arguments, omit `display` for status-only checks, or use `checks: []` to disable them.
Studio runs checks on click through the gateway with the caller's permissions; `display.field` reads a dot-separated JSON field from that result.
