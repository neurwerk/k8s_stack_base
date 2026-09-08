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
                    - "lint.example:443"
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

    def test_mcp_allowlist_is_scoped_to_internal_oauth(self) -> None:
        addresses = (
            "--set-string", "authKeycloak.hostname=identity.example.invalid",
            "--set-string", "frontendLibrechat.agentGateway.hostPort=gateway.example.invalid:8080",
        )
        default = render_librechat_config(*ENABLE_MCP, *addresses)
        internal_entry = '    - "identity.example.invalid:443"\n'
        for mode in ("internal-traefik", "public-dns"):
            for enabled in (True, False):
                with self.subTest(mode=mode, mcp=enabled):
                    config = render_librechat_config(
                        *ENABLE_MCP, *addresses,
                        "--set", f"canonicalEndpointRouting.mode={mode}",
                        "--set", f"mcp.enabled={str(enabled).lower()}",
                    )
                    if not enabled:
                        self.assertNotIn("\nmcpSettings:", config)
                        self.assertNotIn("\nmcpServers:", config)
                        self.assertNotIn(internal_entry, config)
                        continue
                    allowlist = config.split("mcpSettings:\n", 1)[1].split("mcpServers:", 1)[0]
                    expected = '  allowedAddresses:\n    - "gateway.example.invalid:8080"\n'
                    if mode == "internal-traefik":
                        expected += internal_entry
                        self.assertEqual(config, default)
                    else:
                        self.assertEqual(config, default.replace(internal_entry, ""))
                    self.assertEqual(allowlist, expected)

    def test_shared_rejects_invalid_routing_modes_even_without_mcp(self) -> None:
        for mode in ("unknown", ""):
            for enabled in ("true", "false"):
                with self.subTest(mode=mode, mcp=enabled):
                    result = render_chart(
                        "shared",
                        extra_args=(
                            "--set-string", f"canonicalEndpointRouting.mode={mode}",
                            "--set", f"mcp.enabled={enabled}",
                        ),
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(
                        "canonicalEndpointRouting.mode must be internal-traefik or public-dns",
                        result.stderr,
                    )

    def test_shared_config_updates_trigger_app_restart(self) -> None:
        deployment = resource(
            render_chart("app").stdout, "Deployment", "frontend-librechat"
        )
        metadata = deployment.split("\nspec:", 1)[0]
        self.assertIn(
            "    configmap.reloader.stakater.com/reload: frontend-librechat-config-map\n",
            metadata,
        )
        self.assertIn(
            "        - name: config\n          configMap:\n"
            "            name: frontend-librechat-config-map\n",
            deployment,
        )
        self.assertIn(
            "              mountPath: /app/librechat.yaml\n"
            "              subPath: librechat.yaml\n",
            deployment,
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

    def test_grouped_specs_own_selection_without_a_raw_endpoint_row(self) -> None:
        config = render_librechat_config()

        self.assertIn("  modelSelect: false\n", config)
        self.assertIn(
            '        default: ["remote/example/model"]\n        fetch: false\n', config
        )


if __name__ == "__main__":
    unittest.main()
