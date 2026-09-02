"""Rendered configuration contracts for the shared LibreChat chart."""

from __future__ import annotations

import re
import textwrap
import unittest

from .helpers import render_chart, resource


DISABLE_OPTIONAL_CAPABILITIES = (
    "--set",
    "frontendLibrechat.codeInterpreter.enabled=false",
    "--set",
    "frontendLibrechat.rag.enabled=false",
)
ENABLE_MCP = (
    "--set",
    "mcp.enabled=true",
    "--set-string",
    "mcp.servers[0].name=search",
)


def render_librechat_config(*extra_args: str) -> str:
    """Render the embedded application configuration from its ConfigMap."""
    manifest = render_chart("shared", extra_args=extra_args).stdout
    config_map = resource(manifest, "ConfigMap", "frontend-librechat-config-map")
    marker = "  librechat.yaml: |\n"
    if marker not in config_map:
        raise AssertionError("rendered ConfigMap has no librechat.yaml block")
    return textwrap.dedent(config_map.split(marker, 1)[1])


def agent_capabilities(config: str) -> list[str]:
    """Return the exact rendered agent capability allowlist."""
    match = re.search(
        r"(?m)^  agents:\n    capabilities:(?: \[\])?"
        r"(?P<items>(?:\n      - [^\n]+)*)$",
        config,
    )
    if match is None:
        raise AssertionError("rendered config has no agent capabilities")
    return re.findall(r"(?m)^      - ([^\n]+)$", match.group("items"))


class SharedConfigTests(unittest.TestCase):
    """Keep generated agent, reasoning, and MCP settings aligned."""

    def test_disabled_optional_features_grant_no_agent_capabilities(self) -> None:
        config = render_librechat_config(*DISABLE_OPTIONAL_CAPABILITIES)

        self.assertEqual(agent_capabilities(config), [])
        self.assertNotIn("\nmcpSettings:", config)
        self.assertNotIn("\nmcpServers:", config)

    def test_mcp_enables_tools_and_preserves_oauth_routing(self) -> None:
        config = render_librechat_config(
            *DISABLE_OPTIONAL_CAPABILITIES,
            *ENABLE_MCP,
        )

        self.assertEqual(agent_capabilities(config), ["tools"])
        self.assertIn(
            textwrap.dedent(
                """\
                mcpSettings:
                  allowedAddresses:
                    - "infra-agentgateway-gateway.infra-agentgateway.svc.cluster.local:80"
                mcpServers:
                  search:
                    type: streamable-http
                    url: "http://infra-agentgateway-gateway.infra-agentgateway.svc.cluster.local:80/mcp/search"
                    requiresOAuth: true
                    oauth:
                      client_id: "${OPENID_CLIENT_ID}"
                      client_secret: "${OPENID_CLIENT_SECRET}"
                      authorization_url: "https://lint.example/realms/lint/protocol/openid-connect/auth"
                      token_url: "https://lint.example/realms/lint/protocol/openid-connect/token"
                      redirect_uri: "https://librechat.lint.example/api/mcp/search/oauth/callback"
                      scope: openid
                """
            ),
            config,
        )

    def test_all_optional_features_render_their_capabilities(self) -> None:
        config = render_librechat_config(*ENABLE_MCP)

        self.assertEqual(
            agent_capabilities(config), ["tools", "execute_code", "file_search"]
        )

    def test_reasoning_options_are_custom_endpoint_parameters(self) -> None:
        config = render_librechat_config()

        self.assertIn(
            "      customParams:\n"
            "        reasoningKey: reasoning_content\n"
            "        includeReasoningContent: true\n"
            "        includeReasoningHistory: true\n"
            "      addParams:\n"
            "        disableStreaming: true\n",
            config,
        )
        for key in (
            "reasoningKey",
            "includeReasoningContent",
            "includeReasoningHistory",
        ):
            self.assertNotRegex(config, rf"(?m)^      {key}:")


if __name__ == "__main__":
    unittest.main()
