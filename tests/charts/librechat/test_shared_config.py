"""Rendered configuration contracts for the shared LibreChat chart."""

from __future__ import annotations

import re
import textwrap
import unittest

from .helpers import ROOT, render_chart, resource, resources_of_kind

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

    def test_speech_disabled_has_no_config_or_credentials(self) -> None:
        self.assertEqual(
            (ROOT / "charts/librechat/app/templates/_speech.tpl").read_text(),
            (ROOT / "charts/librechat/shared/templates/_speech.tpl").read_text(),
        )
        args = ("--set", "frontendLibrechat.speech.stt.auth.enabled=true",
                "--set", "frontendLibrechat.speech.tts.auth.enabled=true")
        config = render_librechat_config(*args)
        self.assertNotIn("speech:", config)
        self.assertNotIn("conversationMode:", config)
        self.assertNotIn("engineSTT:", config)
        self.assertNotIn("engineTTS:", config)
        self.assertNotIn("apiKey: \"\"", config)
        app = render_chart("app", extra_args=args).stdout
        self.assertEqual(resources_of_kind(app, "ExternalSecret"), [])
        for direction in ("stt", "tts"):
            self.assertNotIn(f"LIBRECHAT_{direction.upper()}_API_KEY", config + app)
            self.assertNotIn(f"frontend-librechat-{direction}-secret", app)

    def test_local_speech_scopes_egress_and_optional_credentials(self) -> None:
        for direction, host in (("stt", "10.20.30.40"), ("tts", "172.16.30.40")):
            for auth in (False, True):
                with self.subTest(direction=direction, auth=auth):
                    prefix = f"frontendLibrechat.speech.{direction}"
                    args = ("--set", f"{prefix}.enabled=true",
                            "--set-string", f"{prefix}.url=http://{host}:8000/v1/audio/{direction}",
                            "--set-string", f"{prefix}.model=speech-model",
                            "--set-string", f"{prefix}.voices[0]=voice-one",
                            "--set", f"{prefix}.auth.enabled={str(auth).lower()}")
                    config = render_librechat_config(*args)
                    self.assertNotIn("allowBrowserSTT:", config)
                    self.assertIn(f'allowedAddresses:\n      - "{host}:8000"', config)
                    self.assertIn(f"engine{direction.upper()}: external", config)
                    self.assertNotIn("conversationMode:", config)
                    app = render_chart("app", extra_args=args).stdout
                    policy = resource(app, "NetworkPolicy", "frontend-librechat-network-policy")
                    self.assertIn(f"cidr: {host}/32\n      ports:\n        - port: 8000\n          protocol: TCP", policy)
                    name = f"frontend-librechat-{direction}-secret"
                    env = f"LIBRECHAT_{direction.upper()}_API_KEY"
                    if auth:
                        self.assertIn(f'apiKey: "${{{env}}}"', config)
                        secret = resource(app, "ExternalSecret", name)
                        self.assertIn("name: frontend-librechat-openbao-secret-store", secret)
                        self.assertIn(f"key: frontend-librechat/external\n        property: {direction}ApiKey", secret)
                        deployment = resource(app, "Deployment", "frontend-librechat")
                        self.assertIn(f"reload: frontend-librechat-secret,{name}", deployment)
                        self.assertIn(f"name: {name}\n                  key: {direction}ApiKey\n                  optional: false", deployment)
                    else:
                        self.assertIn('apiKey: ""', config)
                        self.assertNotIn(env, config + app)
                        self.assertNotIn(name, app)
                    other = "tts" if direction == "stt" else "stt"
                    self.assertNotIn(f"frontend-librechat-{other}-secret", app)

    def test_speech_rejects_unsafe_urls_in_both_charts(self) -> None:
        urls = (
            "http://speech.example.invalid:8000/v1/audio/transcriptions",
            "http://8.8.8.8:8000/v1/audio/transcriptions",
            "http://127.0.0.1:8000/v1/audio/transcriptions",
            "http://169.254.169.254:8000/v1/audio/transcriptions",
            "http://10.20.30.256:8000/v1/audio/transcriptions",
            "http://010.20.30.40:8000/v1/audio/transcriptions",
            "http://10.20.30.40:65536/v1/audio/transcriptions",
            "http://10.20.30.40:0/v1/audio/transcriptions",
            "http://10.20.30.40/v1/audio/transcriptions",
            "http://10.20.30.40:8000/",
            "http://user:password@10.20.30.40:8000/v1/audio/transcriptions",
            "http://10.20.30.40:8000/v1/audio/transcriptions?key=value",
            "http://10.20.30.40:8000/v1/audio/transcriptions#fragment",
            "http://10.20.30.40:8000/${API_PATH}",
        )
        for chart in ("app", "shared"):
            for url in urls:
                with self.subTest(chart=chart, url=url):
                    result = render_chart(chart, check=False, extra_args=(
                        "--set", "frontendLibrechat.speech.stt.enabled=true",
                        "--set-string", "frontendLibrechat.speech.stt.model=speech-model",
                        "--set-string", f"frontendLibrechat.speech.stt.url={url}"))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("frontendLibrechat.speech.stt.url", result.stderr)

    def test_external_tts_requires_approval_https_model_and_voices(self) -> None:
        prefix = "frontendLibrechat.speech.tts"
        args = ("--set", f"{prefix}.enabled=true",
                "--set", f"{prefix}.allowExternal=true",
                "--set", f"{prefix}.url=https://speech.example.invalid/v1/audio/speech",
                "--set", f"{prefix}.model=speech-model",
                "--set", f"{prefix}.voices[0]=voice-one")
        config = render_librechat_config(*args)
        speech = config.split("speech:\n", 1)[1].split("interface:", 1)[0]
        self.assertNotIn("allowedAddresses:", speech)
        self.assertIn('voices: ["voice-one"]', speech)
        self.assertEqual(
            resource(render_chart("app", extra_args=args).stdout, "NetworkPolicy", "frontend-librechat-network-policy"),
            resource(render_chart("app").stdout, "NetworkPolicy", "frontend-librechat-network-policy"),
        )
        for chart in ("app", "shared"):
            for override in ("allowExternal=false", "model=", "voices[0]=",
                             "model=${SPEECH_MODEL}", "voices[0]=${SPEECH_VOICE}",
                             "voices[0]=ALL", "voices[0]=all", "voices[0]=aLl",
                             "provider=other", "url=http://speech.example.invalid/v1/audio/speech",
                             "url=https://speech.example.invalid:8443/v1/audio/speech"):
                with self.subTest(chart=chart, override=override):
                    result = render_chart(chart, check=False, extra_args=(
                        *args, "--set", f"{prefix}.{override}"))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(prefix, result.stderr)

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


if __name__ == "__main__":
    unittest.main()
