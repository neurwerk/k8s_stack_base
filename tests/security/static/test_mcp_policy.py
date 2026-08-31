"""Verify offline MCP authentication, routing, and telemetry policy contracts."""

from __future__ import annotations

import re
import secrets
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT: Path = Path(__file__).resolve().parents[3]
AGENTGATEWAY_CHART: Path = REPOSITORY_ROOT / "charts/agentgateway"
LIBRECHAT_APP_CHART: Path = REPOSITORY_ROOT / "charts/librechat/app"
LIBRECHAT_SHARED_CHART: Path = REPOSITORY_ROOT / "charts/librechat/shared"
STUDIO_API_CHART: Path = REPOSITORY_ROOT / "charts/studio/api"
STUDIO_OIDC_CHART: Path = REPOSITORY_ROOT / "charts/keycloak/oidc/studio"
LIBRECHAT_OIDC_CHART: Path = REPOSITORY_ROOT / "charts/keycloak/oidc/librechat"
SHARED_VALUES: tuple[Path, ...] = (
    REPOSITORY_ROOT / "releases/shared/hostnames.yaml",
    REPOSITORY_ROOT / "releases/shared/oidc-clients.yaml",
    REPOSITORY_ROOT / "releases/shared/resources.yaml",
)


def run_helm(chart: Path, values_files: list[Path], release_name: str) -> str:
    """Render a chart with Helm and return its manifest text.

    Args:
        chart: Chart directory to render.
        values_files: Values files applied in Helm precedence order.
        release_name: Synthetic Helm release name.

    Returns:
        Rendered Kubernetes manifest text.

    Raises:
        RuntimeError: If Helm cannot render the chart.
    """
    command = ["helm", "template", release_name, str(chart)]
    for values_file in values_files:
        command.extend(["--values", str(values_file)])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed:\n{result.stderr}{result.stdout}")
    return result.stdout


def helm_rejects(chart: Path, values_files: list[Path], release_name: str) -> bool:
    """Return whether Helm rejects a deliberately invalid chart configuration.

    Args:
        chart: Chart directory to render.
        values_files: Values files applied in Helm precedence order.
        release_name: Synthetic Helm release name.

    Returns:
        True when Helm returns a non-zero exit status.
    """
    command = ["helm", "template", release_name, str(chart)]
    for values_file in values_files:
        command.extend(["--values", str(values_file)])
    return subprocess.run(command, capture_output=True, text=True, check=False).returncode != 0


def manifest_document(manifest: str, kind: str, name: str) -> str:
    """Return one rendered resource document identified by kind and name.

    Args:
        manifest: Full Helm-rendered manifest.
        kind: Kubernetes resource kind.
        name: Resource metadata name.

    Returns:
        The matching rendered YAML document.

    Raises:
        AssertionError: If no matching resource is present.
    """
    kind_pattern = re.compile(rf"(?m)^kind:\s*{re.escape(kind)}\s*$")
    name_pattern = re.compile(rf"(?m)^  name:\s*{re.escape(name)}\s*$")
    for document in re.split(r"(?m)^---\s*$", manifest):
        if kind_pattern.search(document) and name_pattern.search(document):
            return document
    raise AssertionError(f"Rendered {kind}/{name} was not found:\n{manifest}")


def non_secret_documents(manifest: str) -> str:
    """Return rendered documents other than Kubernetes Secrets.

    Args:
        manifest: Full Helm-rendered manifest.

    Returns:
        Rendered non-Secret YAML documents.
    """
    return "\n---\n".join(
        document
        for document in re.split(r"(?m)^---\s*$", manifest)
        if not re.search(r"(?m)^kind:\s*Secret\s*$", document)
    )


class MCPPolicySecurityTests(unittest.TestCase):
    """Render synthetic MCP fixtures and enforce security invariants."""

    @classmethod
    def setUpClass(cls) -> None:
        """Render charts using fixtures that never touch the repository."""
        cls.temp_directory = tempfile.TemporaryDirectory(prefix="mcp-security-")
        cls.temp_path = Path(cls.temp_directory.name)
        cls.synthetic_secret = secrets.token_urlsafe(32)
        cls.fixture = cls.write_fixture("mcp-fixture.yaml", cls.synthetic_secret)
        cls.agentgateway_manifest = run_helm(
            AGENTGATEWAY_CHART,
            [*SHARED_VALUES, REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml", cls.fixture],
            "mcp-security-agentgateway",
        )
        cls.disabled_observability_fixture = cls.write_disabled_observability_fixture()
        cls.agentgateway_without_observability_manifest = run_helm(
            AGENTGATEWAY_CHART,
            [
                *SHARED_VALUES,
                REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml",
                cls.fixture,
                cls.disabled_observability_fixture,
            ],
            "mcp-security-agentgateway-without-observability",
        )
        cls.librechat_shared_manifest = run_helm(
            LIBRECHAT_SHARED_CHART,
            [*SHARED_VALUES, cls.fixture],
            "mcp-security-librechat-shared",
        )
        cls.librechat_app_fixture = cls.write_librechat_app_fixture()
        cls.librechat_app_manifest = run_helm(
            LIBRECHAT_APP_CHART,
            [*SHARED_VALUES, cls.fixture, cls.librechat_app_fixture],
            "mcp-security-librechat-app",
        )
        cls.studio_api_manifest = run_helm(
            STUDIO_API_CHART,
            [*SHARED_VALUES],
            "mcp-security-studio-api",
        )
        cls.studio_manifest = run_helm(
            STUDIO_OIDC_CHART,
            [*SHARED_VALUES, cls.fixture],
            "mcp-security-studio",
        )
        cls.librechat_oidc_manifest = run_helm(
            LIBRECHAT_OIDC_CHART,
            [*SHARED_VALUES, cls.fixture],
            "mcp-security-librechat-oidc",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        """Delete generated fixtures and rendered values after the test suite."""
        cls.temp_directory.cleanup()

    @classmethod
    def write_fixture(cls, filename: str, synthetic_secret: str) -> Path:
        """Write an MCP fixture with safe, synthetic values to a temporary directory.

        Args:
            filename: Temporary fixture filename.
            synthetic_secret: Runtime-only test credential.

        Returns:
            Path to the generated fixture file.
        """
        path = cls.temp_path / filename
        path.write_text(
            f"""authKeycloak:
  hostname: auth.security.test
  realm: security
  adminUser: security-test-admin
  agentgatewayClientRoles:
    - llm:invoke
    - model:remote/fixture:invoke
    - model:local/fixture:invoke
    - mcp:workload-tools:invoke
    - mcp:static-tools:invoke
  librechatRedirectUri: https://librechat.security.test/oauth/openid/callback
  librechatWebOrigin: https://librechat.security.test
infraAgentgatewayWrapper:
  hostname: agentgateway.security.test
  llamacpp:
    enabled: true
    host: local.fixture.invalid
    port: 11434
externalGateway:
  enabled: true
frontendLibrechat:
  hostname: librechat.security.test
  mcp:
    enabled: true
    servers:
      - name: workload-tools
      - name: static-tools
frontendLibrechatSecrets:
  documentdbUser: librechat
  documentdbPassword: {synthetic_secret}
  openidClientSecret: {synthetic_secret}
guardrails:
  llmPolicyEngine:
    enabled: true
    models:
      - name: remote/fixture
        provider: OpenAI
        model: fixture-remote
        baseURL: https://provider.fixture.invalid
        authSecret: infra-agentgateway-secret
        piiEnabled: true
        contentTracingEnabled: false
        piiReroute: true
      - name: local/fixture
        model: fixture-local
        local: true
        piiEnabled: false
        contentTracingEnabled: true
monitorPiiEngine:
  policy:
    routing:
      defaultTarget: local/fixture
      targets:
        - name: local/fixture
mcp:
  enabled: true
  approvedHosts:
    - mcp.fixture.invalid
  servers:
    - name: workload-tools
      piiEnabled: true
      contentTracingEnabled: false
      port: 8080
      workload:
        image: registry.invalid/mcp-security-fixture:1.0.0
        resources: {{}}
    - name: static-tools
      piiEnabled: false
      contentTracingEnabled: true
      host: mcp.fixture.invalid
      port: 443
      path: /mcp
      protocol: StreamableHTTP
      upstreamAuth:
        header: Authorization
        key: apiKey
infraAgentgatewayWrapperSecrets:
  mcp:
    static-tools:
      apiKey: {synthetic_secret}
""",
            encoding="ascii",
        )
        return path

    @classmethod
    def write_disabled_observability_fixture(cls) -> Path:
        """Write values that disable optional data-plane observability features."""
        path = cls.temp_path / "agentgateway-without-observability.yaml"
        path.write_text(
            """infraAgentgatewayWrapper:
  tracing: null
  costCatalog: false
""",
            encoding="ascii",
        )
        return path

    @classmethod
    def write_librechat_app_fixture(cls) -> Path:
        """Disable the app Gateway independently of AgentGateway's fixture."""
        path = cls.temp_path / "librechat-app.yaml"
        path.write_text(
            """externalGateway:
  enabled: false
""",
            encoding="ascii",
        )
        return path

    def assert_document_contains(self, document: str, text: str) -> None:
        """Assert a resource contains text and print it when the assertion fails.

        Args:
            document: Rendered YAML resource.
            text: Required text.
        """
        self.assertIn(text, document, f"Missing {text!r} in rendered resource:\n{document}")

    def test_authentication_uses_trusted_extauth_identity(self) -> None:
        """Reject client-controlled identity headers and require verified identity."""
        policy = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-auth-ag-policy",
        )
        sanitization = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-remove-untrusted-identity-headers",
        )
        credential_removal = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-remove-caller-credentials",
        )
        self.assert_document_contains(sanitization, "- x-auth-user")
        self.assert_document_contains(sanitization, "- x-user-id")
        self.assert_document_contains(sanitization, "- x-auth-app")
        self.assert_document_contains(sanitization, "- x-auth-permissions")
        self.assert_document_contains(sanitization, "- x-agentgateway-permissions")
        self.assert_document_contains(policy, "contract_version: 'json(response.body).contract_version'")
        self.assert_document_contains(policy, "principal_id: 'json(response.body).principal.id'")
        self.assert_document_contains(policy, "permissions: 'json(response.body).permissions'")
        self.assert_document_contains(
            policy,
            'extauthz.contract_version == 1 && has(extauthz.principal_id) && has(extauthz.permissions) && "llm:invoke" in extauthz.permissions',
        )
        self.assertRegex(
            policy,
            r'jwt\.azp in \["librechat", "agentgateway"\]',
            policy,
        )
        self.assertNotIn('jwt.azp in ["librechat", "studio", "agentgateway"]', policy, policy)
        self.assert_document_contains(policy, "!has(jwt.sub)")
        self.assert_document_contains(policy, "phase: PreRouting")
        self.assertNotIn("allowedResponseHeaders", policy, policy)
        self.assertNotIn("response.headers", policy, policy)
        self.assert_document_contains(credential_removal, "phase: PostRouting")
        self.assert_document_contains(credential_removal, "- Authorization")
        self.assert_document_contains(credential_removal, "- x-api-key")
        self.assertNotRegex(policy, r'request\.headers\["(?:x-auth-user|x-user-id|X-User-ID)"\]', policy)
        self.assertNotIn('jwt.azp in ["*"]', policy, policy)
        self.assertNotIn('jwt.azp == "*"', policy, policy)
        self.assertNotRegex(policy, r"matchExpressions:\n\s+-\s+['\"]?true", policy)

    def test_all_mcp_paths_require_gateway_authenticated_identity(self) -> None:
        """Require Gateway JWT or API-key authentication on every MCP path."""
        policy = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-auth-ag-policy",
        )
        self.assert_document_contains(policy, 'condition: "!has(jwt.sub)"')
        self.assertNotIn("oauth-protected-resource", policy, policy)
        self.assertNotIn("oauth-authorization-server", policy, policy)
        self.assertNotIn("/v1/models", policy, policy)
        self.assertNotIn('request.path.startsWith("/mcp")', policy, policy)

    def test_mcp_catalog_requires_a_public_https_gateway(self) -> None:
        """Reject MCP catalogs that leave external clients without a route."""
        no_external_gateway = self.write_fixture(
            "mcp-no-external-gateway.yaml",
            self.synthetic_secret,
        )
        no_external_gateway.write_text(
            no_external_gateway.read_text(encoding="ascii").replace(
                "externalGateway:\n  enabled: true",
                "externalGateway:\n  enabled: false",
            ),
            encoding="ascii",
        )
        base_values = [*SHARED_VALUES, REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml"]
        self.assertTrue(
            helm_rejects(
                AGENTGATEWAY_CHART,
                [*base_values, no_external_gateway],
                "mcp-no-external-gateway",
            )
        )

    def test_public_gateway_forwards_mcp_clients_to_agentgateway(self) -> None:
        """Expose the authenticated internal Gateway through a TLS entrypoint."""
        gateway = manifest_document(
            self.agentgateway_manifest,
            "Gateway",
            "infra-agentgateway-external-gateway",
        )
        route = manifest_document(
            self.agentgateway_manifest,
            "HTTPRoute",
            "infra-agentgateway-external-httproute",
        )
        self.assert_document_contains(gateway, "gatewayClassName: traefik")
        self.assert_document_contains(gateway, "protocol: HTTPS")
        self.assert_document_contains(gateway, "hostname: agentgateway.security.test")
        self.assert_document_contains(route, "hostnames:")
        self.assert_document_contains(route, "- agentgateway.security.test")
        self.assert_document_contains(route, "name: infra-agentgateway-gateway")

    def test_data_plane_service_stays_private_with_or_without_observability(self) -> None:
        """Keep generated data-plane access behind Traefik and approved callers."""
        parameters = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayParameters",
            "infra-agentgateway-parameters",
        )
        gateway = manifest_document(
            self.agentgateway_manifest,
            "Gateway",
            "infra-agentgateway-gateway",
        )
        network_policy = manifest_document(
            self.agentgateway_manifest,
            "NetworkPolicy",
            "infra-agentgateway-data-plane-network-policy",
        )
        disabled_parameters = manifest_document(
            self.agentgateway_without_observability_manifest,
            "AgentgatewayParameters",
            "infra-agentgateway-parameters",
        )
        disabled_gateway = manifest_document(
            self.agentgateway_without_observability_manifest,
            "Gateway",
            "infra-agentgateway-gateway",
        )

        for document in (parameters, disabled_parameters):
            self.assert_document_contains(document, "service:")
            self.assert_document_contains(document, "type: ClusterIP")
            self.assertNotRegex(document, r"(?m)^\s*type:\s*(?:LoadBalancer|NodePort)\s*$", document)
            self.assertRegex(
                document,
                r"(?s)rawConfig:\s+frontendPolicies:\s+http:\s+maxBufferSize: 6291456",
            )
        self.assertRegex(
            parameters,
            r"(?s)maxBufferSize: 6291456\s+config:\s+tracing:\s+otlpEndpoint:",
        )
        self.assertNotIn("tracing:", disabled_parameters)
        for document in (gateway, disabled_gateway):
            self.assert_document_contains(document, "parametersRef:")
            self.assert_document_contains(document, "name: infra-agentgateway-parameters")
            self.assert_document_contains(document, "kind: AgentgatewayParameters")

        self.assert_document_contains(
            network_policy,
            "gateway.networking.k8s.io/gateway-name: infra-agentgateway-gateway",
        )
        self.assert_document_contains(network_policy, "port: 80")
        for namespace, workload in (
            ("kube-system", "traefik"),
            ("frontend-librechat", "frontend-librechat"),
            ("frontend-dify", "api"),
            ("frontend-dify", "worker"),
            ("frontend-dify", "plugin-daemon"),
        ):
            self.assert_document_contains(
                network_policy,
                f"kubernetes.io/metadata.name: {namespace}",
            )
            self.assert_document_contains(network_policy, f"app.kubernetes.io/name: {workload}")
        self.assertRegex(
            network_policy,
            r"(?s)kubernetes.io/metadata.name: monitor-kube-prometheus-stack"
            r".*?app.kubernetes.io/name: prometheus"
            r".*?operator.prometheus.io/name: monitor-kube-prometheus-st"
            r".*?ports:.*?- port: metrics\s+protocol: TCP",
            network_policy,
        )
        self.assertEqual(network_policy.count("namespaceSelector:"), 6, network_policy)
        self.assertNotIn("kubernetes.io/metadata.name: frontend-studio", network_policy)
        self.assertNotIn("app.kubernetes.io/name: studio-api", network_policy)
        self.assertNotIn("ipBlock:", network_policy, network_policy)
        self.assertNotIn("namespaceSelector: {}", network_policy, network_policy)
        self.assertNotIn("podSelector: {}", network_policy, network_policy)

    def test_studio_api_has_no_direct_agentgateway_path(self) -> None:
        """Remove direct Studio calls while retaining PII and API-key dependencies."""
        studio_egress = manifest_document(
            self.studio_api_manifest,
            "NetworkPolicy",
            "frontend-studio-api-egress-network-policy",
        )
        gateway_auth = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-auth-ag-policy",
        )
        remote_model = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayModel",
            "remote-fixture-remote",
        )

        self.assertNotIn("ipBlock:", studio_egress, studio_egress)
        self.assertNotIn("0.0.0.0/0", studio_egress, studio_egress)
        self.assertNotIn("K8S_STUDIO_AGENTGATEWAY_URL", self.studio_api_manifest)
        self.assertNotIn("K8S_STUDIO_AGENTGATEWAY_LOCAL_MODEL_URL", self.studio_api_manifest)
        for destination in (
            """kubernetes.io/metadata.name: monitor-pii-engine
          podSelector:
            matchLabels:
              app.kubernetes.io/name: pii-engine
              app.kubernetes.io/component: runtime
      ports:
        # The PII Engine Service maps port 443 to Pod port 8443.
        - port: 8443""",
            """kubernetes.io/metadata.name: auth-keycloak
          podSelector:
            matchLabels:
              app: auth-keycloak-keycloak-app
      ports:
        # The Keycloak Service maps port 80 to Pod port 8080.
        - port: 8080""",
            """kubernetes.io/metadata.name: auth-keycloak-api-key-bridge
          podSelector:
            matchLabels:
              app.kubernetes.io/name: auth-keycloak-api-key-bridge
      ports:
        # The API-key bridge Service maps port 3010 to Pod port 8000.
        - port: 8000""",
            """kubernetes.io/metadata.name: monitor-langfuse
          podSelector:
            matchLabels:
              app.kubernetes.io/name: langfuse
              app: web
      ports:
        - port: 3000""",
            """kubernetes.io/metadata.name: monitor-opensearch
          podSelector:
            matchLabels:
              app.kubernetes.io/name: opensearch
      ports:
        - port: 9200""",
        ):
            self.assert_document_contains(studio_egress, destination)
        self.assert_document_contains(studio_egress, "kubernetes.io/metadata.name: kube-system")
        self.assertEqual(studio_egress.count("namespaceSelector:"), 6, studio_egress)
        self.assertNotIn("kubernetes.io/metadata.name: infra-agentgateway", studio_egress)
        self.assertNotIn("app.kubernetes.io/name: infra-agentgateway-gateway", studio_egress)
        self.assertNotIn("\n        - port: 3010\n", studio_egress, studio_egress)
        self.assert_document_contains(
            gateway_auth,
            'jwt.azp in ["librechat", "agentgateway"]',
        )
        self.assertNotIn('"studio"', gateway_auth, gateway_auth)
        self.assert_document_contains(gateway_auth, '"llm:invoke" in jwt.resource_access.agentgateway.roles')
        self.assert_document_contains(remote_model, "model:remote/fixture:invoke")
        self.assert_document_contains(remote_model, "jwt.resource_access.agentgateway.roles")
        self.assert_document_contains(remote_model, "extauthz.permissions")

    def test_mcp_routes_require_resource_permissions_without_passthrough(self) -> None:
        """Require exact routes, focused permission, and fail-closed route extProc."""
        for name in ("workload-tools", "static-tools"):
            route = manifest_document(
                self.agentgateway_manifest,
                "HTTPRoute",
                f"mcp-{name}-route",
            )
            backend = manifest_document(
                self.agentgateway_manifest,
                "AgentgatewayBackend",
                f"mcp-{name}-be",
            )
            permission = manifest_document(
                self.agentgateway_manifest,
                "AgentgatewayPolicy",
                f"mcp-{name}-policy",
            )
            self.assert_document_contains(route, f'value: "/mcp/{name}"')
            self.assert_document_contains(route, "type: Exact")
            self.assertNotRegex(route, r'(?m)^\s+value:\s+"/mcp"\s*$', route)
            self.assert_document_contains(backend, "failureMode: FailClosed")
            self.assert_document_contains(backend, "prefixMode: Always")
            self.assert_document_contains(permission, "phase: PostRouting")
            self.assert_document_contains(permission, f"mcp:{name}:invoke")
            self.assert_document_contains(permission, "jwt.resource_access.agentgateway.roles")
            self.assert_document_contains(permission, "extauthz.permissions")
            self.assertNotIn("json(request.body)", permission)
            self.assertNotIn("mcp-protocol-version", permission)
            self.assert_document_contains(permission, "name: infra-agentgateway-extproc-backend")
            self.assert_document_contains(permission, "failureMode: FailClosed")
            self.assert_document_contains(permission, "neurwerk.destination_policy")
            self.assert_document_contains(permission, 'contract_version: "1"')
            self.assert_document_contains(permission, "destination_kind: '\"mcp\"'")
            self.assert_document_contains(permission, f'destination_id: "\\"{name}\\""')
            self.assert_document_contains(permission, "requestBodyMode: Buffered")
            self.assert_document_contains(permission, "responseBodyMode: FullDuplexStreamed")
            self.assert_document_contains(permission, "allowModeOverride: true")
            self.assertNotIn("authentication:", backend, backend)
            self.assertNotIn("authorization:", backend, backend)
            self.assertNotIn("passthrough", backend, backend)
            self.assertNotRegex(backend, r'request\.headers\["(?:x-auth-user|x-user-id|X-User-ID)"\]', backend)

    def test_models_require_derived_permissions_and_keep_provider_auth(self) -> None:
        """Authorize concrete targets while keeping virtual models policy-free."""
        remote_target = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayModel",
            "remote-fixture-remote",
        )
        local_fallback = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayModel",
            "remote-fixture-local",
        )
        virtual_model = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayModel",
            "remote-fixture",
        )
        local_model = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayModel",
            "local-fixture",
        )
        for model in (remote_target, local_fallback):
            self.assert_document_contains(model, "model:remote/fixture:invoke")
            self.assert_document_contains(model, "jwt.resource_access.agentgateway.roles")
            self.assert_document_contains(model, "extauthz.permissions")
        self.assert_document_contains(local_model, "model:local/fixture:invoke")
        self.assert_document_contains(remote_target, "auth:\n      secretRef:\n        name: infra-agentgateway-secret")
        self.assertNotIn("policies:", virtual_model, virtual_model)
        self.assert_document_contains(virtual_model, "name: remote-fixture-local")

    def test_unsafe_model_permission_identifier_fails_rendering(self) -> None:
        """Reject public model identifiers that cannot become bridge permissions."""
        unsafe_model = self.write_fixture("unsafe-model.yaml", self.synthetic_secret)
        unsafe_model.write_text(
            unsafe_model.read_text(encoding="ascii").replace("remote/fixture", "remote/fixture:bad"),
            encoding="ascii",
        )
        base_values = [*SHARED_VALUES, REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml"]
        self.assertTrue(
            helm_rejects(
                AGENTGATEWAY_CHART,
                [*base_values, unsafe_model],
                "unsafe-model-permission",
            )
        )

    def test_model_ids_require_valid_generated_kubernetes_identities(self) -> None:
        """Reject source IDs whose normalized model resources or labels are invalid."""
        invalid_ids = (
            "remote/fixture/",
            "remote/fixture_",
            "remote/fixture.",
            "remote..fixture",
            "a" * 51,
            "a" * 64,
        )
        base_values = [*SHARED_VALUES, REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml"]
        for index, model_id in enumerate(invalid_ids):
            with self.subTest(model_id=model_id):
                fixture = self.write_fixture(f"invalid-model-identity-{index}.yaml", self.synthetic_secret)
                fixture.write_text(
                    fixture.read_text(encoding="ascii").replace("remote/fixture", model_id),
                    encoding="ascii",
                )
                self.assertTrue(
                    helm_rejects(
                        AGENTGATEWAY_CHART,
                        [*base_values, fixture],
                        f"invalid-model-identity-{index}",
                    )
                )

    def test_current_model_ids_generate_valid_kubernetes_identities(self) -> None:
        """Accept current caller IDs and render every public/internal identity safely."""
        current_ids = (
            "remote/deepseek/v4-pro",
            "remote/openrouter/deepseek-v4-flash",
            "remote/openrouter/nemotron-3.5-lightning-free",
            "local/llama3.2-3b",
        )
        base_values = [*SHARED_VALUES, REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml"]
        resource_pattern = re.compile(
            r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*$"
        )
        label_pattern = re.compile(r"^[a-z0-9](?:[-a-z0-9_.]*[a-z0-9])?$")
        for index, model_id in enumerate(current_ids):
            with self.subTest(model_id=model_id):
                fixture = self.write_fixture(f"valid-model-identity-{index}.yaml", self.synthetic_secret)
                fixture.write_text(
                    fixture.read_text(encoding="ascii").replace("remote/fixture", model_id),
                    encoding="ascii",
                )
                manifest = run_helm(
                    AGENTGATEWAY_CHART,
                    [*base_values, fixture],
                    f"valid-model-identity-{index}",
                )
                base_name = re.sub(r"[^a-z0-9.-]", "-", model_id.lower())
                for generated_name in (
                    base_name,
                    f"{base_name}-remote",
                    f"{base_name}-local",
                ):
                    model = manifest_document(manifest, "AgentgatewayModel", generated_name)
                    label_value = f"model-{generated_name}"
                    self.assertLessEqual(len(generated_name), 253)
                    self.assertRegex(generated_name, resource_pattern)
                    self.assertTrue(all(len(segment) <= 63 for segment in generated_name.split(".")))
                    self.assertLessEqual(len(label_value), 63)
                    self.assertRegex(label_value, label_pattern)
                    self.assertIn(f"app.kubernetes.io/name: {label_value}", model)
                public_model = manifest_document(manifest, "AgentgatewayModel", base_name)
                self.assertIn(f'model: "{model_id}"', public_model)

    def test_model_and_mcp_extproc_have_separate_scopes(self) -> None:
        """Use model PreRouting and exact route-owned MCP PostRouting streams."""
        policy = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-policy-extproc",
        )
        self.assert_document_contains(policy, 'condition: \'request.method == "POST" && request.path.startsWith("/v1/")\'')
        self.assert_document_contains(policy, "failureMode: FailClosed")
        self.assertNotIn("extProc:\n      backendRef", policy, policy)
        for name in ("workload-tools", "static-tools"):
            route_policy = manifest_document(
                self.agentgateway_manifest,
                "AgentgatewayPolicy",
                f"mcp-{name}-policy",
            )
            self.assert_document_contains(route_policy, "phase: PostRouting")
            self.assert_document_contains(route_policy, "infra-agentgateway-extproc-backend")
            self.assertNotIn("destination_kind: '\"model\"'", route_policy)

    def test_destination_metadata_and_content_tracing_follow_effective_flags(self) -> None:
        """Key rendered policy and tracing catalogs by exact public destination IDs."""
        model_policy = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-policy-extproc",
        )
        workload_policy = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayPolicy",
            "mcp-workload-tools-policy",
        )
        static_policy = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayPolicy",
            "mcp-static-tools-policy",
        )
        virtual_model = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayModel",
            "remote-fixture",
        )
        workload_backend = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayBackend",
            "mcp-workload-tools-be",
        )
        static_backend = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayBackend",
            "mcp-static-tools-be",
        )
        parameters = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayParameters",
            "infra-agentgateway-parameters",
        )
        normalized_parameters = " ".join(parameters.split())

        self.assertIn(
            'models: "{\\"local/fixture\\":false,\\"remote/fixture\\":true}"',
            model_policy,
        )
        self.assertIn('pii_enabled: "true"', workload_policy)
        self.assertIn('pii_enabled: "false"', static_policy)
        for policy in (model_policy, workload_policy, static_policy):
            self.assertIn("principal_id: 'has(jwt.sub) ? jwt.sub : extauthz.principal_id'", policy)
            self.assertNotIn("x-auth-user", policy)
            self.assertNotIn("x-user-id", policy)
            self.assertIn("responseBodyMode: FullDuplexStreamed", policy)
            self.assertIn("allowModeOverride: true", policy)
        self.assertIn('model: "remote/fixture"', virtual_model)
        self.assertIn("name: remote-fixture-remote", virtual_model)
        self.assertIn("name: remote-fixture-local", virtual_model)
        self.assertIn('- name: "workload-tools"', workload_backend)
        self.assertIn('- name: "static-tools"', static_backend)
        self.assertIn('metadata.agentgateway_user_model in ["remote/fixture"]', normalized_parameters)
        self.assertIn('mcp.tool.target in ["workload-tools"]', normalized_parameters)
        self.assertNotIn("fixture-remote", parameters)
        self.assertNotIn("fixture-local", parameters)
        for field in (
            "gen_ai.prompt",
            "gen_ai.completion",
            "langfuse.observation.input",
            "langfuse.observation.output",
        ):
            self.assertIn(field, parameters)
        self.assertIn(": null", parameters)

    def test_pinned_agentgateway_package_matches_rendered_contract(self) -> None:
        """Keep wrapper metadata and the vendored upstream package on v1.4.1."""
        chart_metadata = (AGENTGATEWAY_CHART / "Chart.yaml").read_text(encoding="utf-8")
        chart_lock = (AGENTGATEWAY_CHART / "Chart.lock").read_text(encoding="ascii")
        package = AGENTGATEWAY_CHART / "charts/agentgateway-1.4.1.tgz"

        self.assertIn("version: 0.7.13", chart_metadata)
        self.assertEqual(chart_metadata.count('1.4.1'), 2)
        self.assertIn("version: 1.4.1", chart_lock)
        with tarfile.open(package, mode="r:gz") as archive:
            upstream_chart = archive.extractfile("agentgateway/Chart.yaml")
            self.assertIsNotNone(upstream_chart)
            self.assertIn(b"version: 1.4.1", upstream_chart.read())

    def test_omitted_destination_flags_default_to_true(self) -> None:
        """Retain compatibility behavior only when controls are omitted."""
        fixture = self.write_fixture("omitted-flags.yaml", self.synthetic_secret)
        fixture.write_text(
            re.sub(
                r"^\s+(?:piiEnabled|contentTracingEnabled): (?:true|false)\n",
                "",
                fixture.read_text(encoding="ascii"),
                flags=re.MULTILINE,
            ),
            encoding="ascii",
        )
        manifest = run_helm(
            AGENTGATEWAY_CHART,
            [*SHARED_VALUES, REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml", fixture],
            "destination-defaults",
        )
        model_policy = manifest_document(
            manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-policy-extproc",
        )
        parameters = manifest_document(
            manifest,
            "AgentgatewayParameters",
            "infra-agentgateway-parameters",
        )
        self.assertIn(
            'models: "{\\"local/fixture\\":true,\\"remote/fixture\\":true}"',
            model_policy,
        )
        for name in ("workload-tools", "static-tools"):
            policy = manifest_document(manifest, "AgentgatewayPolicy", f"mcp-{name}-policy")
            self.assertIn('pii_enabled: "true"', policy)
        normalized_parameters = " ".join(parameters.split())
        self.assertIn("metadata.agentgateway_user_model in []", normalized_parameters)
        self.assertIn("mcp.tool.target in []", normalized_parameters)

    def test_model_and_mcp_render_every_destination_flag_combination(self) -> None:
        """Render the independent PII and tracing matrix for both protocols."""
        for pii_enabled in (True, False):
            for tracing_enabled in (True, False):
                with self.subTest(pii=pii_enabled, tracing=tracing_enabled):
                    fixture = self.write_fixture(
                        f"destination-matrix-{int(pii_enabled)}-{int(tracing_enabled)}.yaml",
                        self.synthetic_secret,
                    )
                    contents = fixture.read_text(encoding="ascii")
                    model_flags = "\n".join(
                        (
                            f"        piiEnabled: {str(pii_enabled).lower()}",
                            f"        contentTracingEnabled: {str(tracing_enabled).lower()}",
                        )
                    )
                    mcp_flags = "\n".join(
                        (
                            f"      piiEnabled: {str(pii_enabled).lower()}",
                            f"      contentTracingEnabled: {str(tracing_enabled).lower()}",
                        )
                    )
                    contents = contents.replace(
                        "        piiEnabled: false\n        contentTracingEnabled: true",
                        model_flags,
                        1,
                    ).replace(
                        "      piiEnabled: false\n      contentTracingEnabled: true",
                        mcp_flags,
                        1,
                    )
                    fixture.write_text(contents, encoding="ascii")
                    manifest = run_helm(
                        AGENTGATEWAY_CHART,
                        [
                            *SHARED_VALUES,
                            REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml",
                            fixture,
                        ],
                        f"destination-matrix-{int(pii_enabled)}-{int(tracing_enabled)}",
                    )
                    model_policy = manifest_document(
                        manifest,
                        "AgentgatewayPolicy",
                        "infra-agentgateway-policy-extproc",
                    )
                    mcp_policy = manifest_document(
                        manifest,
                        "AgentgatewayPolicy",
                        "mcp-static-tools-policy",
                    )
                    parameters = manifest_document(
                        manifest,
                        "AgentgatewayParameters",
                        "infra-agentgateway-parameters",
                    )
                    self.assertIn(
                        f'\\"local/fixture\\":{str(pii_enabled).lower()}',
                        model_policy,
                    )
                    self.assertIn(
                        f'pii_enabled: "{str(pii_enabled).lower()}"',
                        mcp_policy,
                    )
                    model_disabled = (
                        '["remote/fixture","local/fixture"]'
                        if not tracing_enabled
                        else '["remote/fixture"]'
                    )
                    mcp_disabled = (
                        '["workload-tools","static-tools"]'
                        if not tracing_enabled
                        else '["workload-tools"]'
                    )
                    normalized_parameters = " ".join(parameters.split())
                    self.assertIn(
                        f"metadata.agentgateway_user_model in {model_disabled}",
                        normalized_parameters,
                    )
                    self.assertIn(
                        f"mcp.tool.target in {mcp_disabled}",
                        normalized_parameters,
                    )
                    self.assertEqual(parameters.count(": null'"), 4)
                    expected_occurrences = 0 if tracing_enabled else 2
                    self.assertEqual(parameters.count("local/fixture"), expected_occurrences)
                    self.assertEqual(parameters.count("static-tools"), expected_occurrences)

    def test_destination_flags_require_strict_booleans(self) -> None:
        """Reject string and numeric values instead of coercing policy controls."""
        replacements = (
            ("        piiEnabled: true", '        piiEnabled: "true"'),
            ("        contentTracingEnabled: false", "        contentTracingEnabled: 1"),
            ("      piiEnabled: true", '      piiEnabled: "false"'),
            ("      contentTracingEnabled: false", "      contentTracingEnabled: 0"),
        )
        base_values = [*SHARED_VALUES, REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml"]
        for index, (valid, invalid) in enumerate(replacements):
            with self.subTest(field=valid.strip().split(":", maxsplit=1)[0], index=index):
                fixture = self.write_fixture(f"invalid-destination-flag-{index}.yaml", self.synthetic_secret)
                contents = fixture.read_text(encoding="ascii")
                self.assertIn(valid, contents)
                fixture.write_text(contents.replace(valid, invalid, 1), encoding="ascii")
                self.assertTrue(
                    helm_rejects(
                        AGENTGATEWAY_CHART,
                        [*base_values, fixture],
                        f"invalid-destination-flag-{index}",
                    )
                )

    def test_destination_identity_and_dispatcher_contracts_fail_closed(self) -> None:
        """Reject ambiguous names, unsupported protocol, and missing dispatcher."""
        replacements = (
            ("      - name: local/fixture", "      - name: remote-fixture"),
            ("      - name: local/fixture", "      - name: remote/fixture"),
            (
                "    - name: static-tools\n      piiEnabled: false",
                "    - name: workload-tools\n      piiEnabled: false",
            ),
            (
                "    - name: static-tools\n      piiEnabled: false",
                "    - name: BAD_tools\n      piiEnabled: false",
            ),
            ("mcp:\n  enabled: true", 'mcp:\n  enabled: true\n  protocolVersion: "2025-06-18"'),
            (
                "guardrails:\n  llmPolicyEngine:\n    enabled: true",
                "guardrails:\n  llmPolicyEngine:\n    enabled: false",
            ),
            (
                "        piiEnabled: true\n        contentTracingEnabled: false\n        piiReroute: true",
                "        piiEnabled: false\n        contentTracingEnabled: false\n        piiReroute: true",
            ),
            (
                "    - name: static-tools\n      piiEnabled: false",
                f"    - name: {'a' * 49}\n      piiEnabled: false",
            ),
        )
        base_values = [*SHARED_VALUES, REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml"]
        for index, (valid, invalid) in enumerate(replacements):
            with self.subTest(case=index):
                fixture = self.write_fixture(f"invalid-destination-contract-{index}.yaml", self.synthetic_secret)
                contents = fixture.read_text(encoding="ascii")
                self.assertIn(valid, contents)
                fixture.write_text(contents.replace(valid, invalid, 1), encoding="ascii")
                self.assertTrue(
                    helm_rejects(
                        AGENTGATEWAY_CHART,
                        [*base_values, fixture],
                        f"invalid-destination-contract-{index}",
                    )
                )

    def test_static_mcp_id_may_be_dotted_without_an_alias(self) -> None:
        """Render one canonical dotted static ID through routes, targets, and callbacks."""
        fixture = self.write_fixture("dotted-static-mcp.yaml", self.synthetic_secret)
        fixture.write_text(
            fixture.read_text(encoding="ascii").replace("static-tools", "static.tools"),
            encoding="ascii",
        )
        base_values = [*SHARED_VALUES, REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml"]
        agentgateway = run_helm(
            AGENTGATEWAY_CHART,
            [*base_values, fixture],
            "dotted-static-mcp-agentgateway",
        )
        route = manifest_document(agentgateway, "HTTPRoute", "mcp-static.tools-route")
        backend = manifest_document(agentgateway, "AgentgatewayBackend", "mcp-static.tools-be")
        policy = manifest_document(agentgateway, "AgentgatewayPolicy", "mcp-static.tools-policy")
        self.assertIn('value: "/mcp/static.tools"', route)
        self.assertIn('- name: "static.tools"', backend)
        self.assertIn('destination_id: "\\"static.tools\\""', policy)
        self.assertNotIn("name: mcp-static.tools-svc", agentgateway)

        librechat = run_helm(
            LIBRECHAT_SHARED_CHART,
            [*SHARED_VALUES, fixture],
            "dotted-static-mcp-librechat",
        )
        config = manifest_document(librechat, "ConfigMap", "frontend-librechat-config-map")
        self.assertIn("/mcp/static.tools", config)
        oidc = run_helm(
            LIBRECHAT_OIDC_CHART,
            [*SHARED_VALUES, fixture],
            "dotted-static-mcp-oidc",
        )
        job = manifest_document(oidc, "Job", "auth-keycloak-librechat-oidc-job")
        self.assertIn("/api/mcp/static.tools/oauth/callback", job)

    def test_workload_mcp_id_must_fit_service_and_container_dns_labels(self) -> None:
        """Keep direct workload Service and container identities valid without aliases."""
        deployment = manifest_document(
            self.agentgateway_manifest,
            "Deployment",
            "mcp-workload-tools-deploy",
        )
        service = manifest_document(
            self.agentgateway_manifest,
            "Service",
            "mcp-workload-tools-svc",
        )
        dns_label = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
        for name in ("mcp-workload-tools", "mcp-workload-tools-svc"):
            self.assertLessEqual(len(name), 63)
            self.assertRegex(name, dns_label)
        self.assertIn("- name: mcp-workload-tools", deployment)
        self.assertIn("name: mcp-workload-tools-svc", service)

        fixture = self.write_fixture("dotted-workload-mcp.yaml", self.synthetic_secret)
        fixture.write_text(
            fixture.read_text(encoding="ascii").replace("workload-tools", "workload.tools"),
            encoding="ascii",
        )
        base_values = [*SHARED_VALUES, REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml"]
        self.assertTrue(
            helm_rejects(
                AGENTGATEWAY_CHART,
                [*base_values, fixture],
                "dotted-workload-mcp",
            )
        )

    def test_mcp_backends_are_allowlisted_and_workloads_are_private(self) -> None:
        """Reject arbitrary upstreams and direct workload Service exposure."""
        static_backend = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayBackend",
            "mcp-static-tools-be",
        )
        workload_backend = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayBackend",
            "mcp-workload-tools-be",
        )
        service = manifest_document(
            self.agentgateway_manifest,
            "Service",
            "mcp-workload-tools-svc",
        )
        network_policy = manifest_document(
            self.agentgateway_manifest,
            "NetworkPolicy",
            "mcp-workload-tools-netpol",
        )
        self.assert_document_contains(static_backend, 'host: "mcp.fixture.invalid"')
        self.assertNotIn("dynamic", static_backend.lower(), static_backend)
        self.assert_document_contains(static_backend, "secretRef:")
        self.assert_document_contains(static_backend, "name: mcp-static-tools-secret")
        self.assert_document_contains(static_backend, 'key: "apiKey"')
        self.assert_document_contains(workload_backend, "name: mcp-workload-tools-svc")
        self.assertNotIn("credentials:", workload_backend, workload_backend)
        workload_target = workload_backend.split("\n  policies:\n    mcp:", maxsplit=1)[0]
        self.assertNotIn("\n          policies:", workload_target, workload_target)
        self.assert_document_contains(service, "type: ClusterIP")
        self.assertNotIn("LoadBalancer", service, service)
        self.assertNotIn("NodePort", service, service)
        self.assert_document_contains(network_policy, "app.kubernetes.io/name: infra-agentgateway-gateway")

    def test_invalid_mcp_target_combinations_are_rejected(self) -> None:
        """Fail Helm rendering for unapproved hosts and workload credentials."""
        unapproved = self.write_fixture(
            "unapproved-host.yaml",
            self.synthetic_secret,
        )
        unapproved.write_text(
            unapproved.read_text(encoding="ascii").replace(
                "host: mcp.fixture.invalid",
                "host: unapproved.fixture.invalid",
            ),
            encoding="ascii",
        )
        workload_auth = self.write_fixture("workload-auth.yaml", self.synthetic_secret)
        workload_auth.write_text(
            workload_auth.read_text(encoding="ascii").replace(
                "        resources: {}",
                "        resources: {}\n      upstreamAuth:\n        header: Authorization",
                1,
            ),
            encoding="ascii",
        )
        missing_local_host = self.write_fixture("missing-local-host.yaml", self.synthetic_secret)
        missing_local_host.write_text(
            missing_local_host.read_text(encoding="ascii").replace(
                "    host: local.fixture.invalid",
                '    host: ""',
            ),
            encoding="ascii",
        )
        base_values = [*SHARED_VALUES, REPOSITORY_ROOT / "releases/shared/default-pii-settings.yaml"]
        self.assertTrue(helm_rejects(AGENTGATEWAY_CHART, [*base_values, unapproved], "mcp-unapproved-host"))
        self.assertTrue(helm_rejects(AGENTGATEWAY_CHART, [*base_values, workload_auth], "mcp-workload-auth"))
        self.assertTrue(
            helm_rejects(AGENTGATEWAY_CHART, [*base_values, missing_local_host], "missing-local-host")
        )

    def test_credentials_stay_in_secret_resources(self) -> None:
        """Keep runtime-only fixture credentials out of rendered non-Secret configuration."""
        secret = manifest_document(
            self.agentgateway_manifest,
            "Secret",
            "mcp-static-tools-secret",
        )
        non_secret = non_secret_documents(self.agentgateway_manifest)
        librechat_secret = manifest_document(
            self.librechat_shared_manifest,
            "Secret",
            "frontend-librechat-secret",
        )
        self.assert_document_contains(secret, self.synthetic_secret)
        self.assert_document_contains(librechat_secret, self.synthetic_secret)
        self.assertNotIn(self.synthetic_secret, non_secret, non_secret)
        self.assertNotIn(
            self.synthetic_secret,
            non_secret_documents(self.librechat_shared_manifest),
            self.librechat_shared_manifest,
        )
        self.assertNotIn(
            self.synthetic_secret,
            non_secret_documents(self.librechat_app_manifest),
            self.librechat_app_manifest,
        )
        self.assertNotIn("brave", non_secret.lower(), non_secret)

    def test_librechat_uses_internal_gateway_mcp_servers(self) -> None:
        """Use the internal Gateway while retaining the existing OAuth client."""
        config = manifest_document(
            self.librechat_shared_manifest,
            "ConfigMap",
            "frontend-librechat-config-map",
        )
        mcp_config = config.split("mcpServers:", maxsplit=1)[1]
        self.assert_document_contains(config, "mcpSettings:")
        internal_origin = "http://infra-agentgateway-gateway.infra-agentgateway.svc.cluster.local:80"
        self.assert_document_contains(
            config,
            '"infra-agentgateway-gateway.infra-agentgateway.svc.cluster.local:80"',
        )
        for name in ("workload-tools", "static-tools"):
            self.assert_document_contains(config, f"{internal_origin}/mcp/{name}")
            self.assert_document_contains(
                config,
                f"https://librechat.security.test/api/mcp/{name}/oauth/callback",
            )
        self.assert_document_contains(config, 'client_id: "${OPENID_CLIENT_ID}"')
        self.assert_document_contains(config, 'client_secret: "${OPENID_CLIENT_SECRET}"')
        self.assert_document_contains(config, "requiresOAuth: true")
        self.assert_document_contains(
            config,
            'authorization_url: "https://auth.security.test/realms/security/protocol/openid-connect/auth"',
        )
        self.assert_document_contains(
            config,
            'token_url: "https://auth.security.test/realms/security/protocol/openid-connect/token"',
        )
        self.assert_document_contains(config, "scope: openid")
        self.assertNotIn("LIBRECHAT_OPENID_ACCESS_TOKEN", mcp_config, mcp_config)
        self.assertNotIn("https://agentgateway.security.test/mcp", config, config)
        self.assertNotIn("mcp.fixture.invalid", config, config)

    def test_librechat_oidc_client_accepts_mcp_callbacks(self) -> None:
        """Allow the existing confidential LibreChat client to handle MCP OAuth callbacks."""
        job = manifest_document(
            self.librechat_oidc_manifest,
            "Job",
            "auth-keycloak-librechat-oidc-job",
        )
        self.assert_document_contains(
            job,
            'value: "https://librechat.security.test/oauth/openid/callback"',
        )
        for server in ("workload-tools", "static-tools"):
            self.assert_document_contains(
                job,
                f"https://librechat.security.test/api/mcp/{server}/oauth/callback",
            )
        self.assertRegex(
            job,
            r'name: KC_WEB_ORIGINS\n\s+value: "\[\\"https://librechat\.security\.test\\"\]"',
            job,
        )
        self.assertNotIn(',\"\"', job)
        self.assertNotIn("https://librechat.security.test/*", job)
        self.assert_document_contains(job, "value: '[\"agentgateway\"]'")
        self.assert_document_contains(job, "name: KC_CLIENT_SECRET")
        self.assert_document_contains(job, "name: auth-keycloak-openbao-secret")
        self.assert_document_contains(job, "key: librechatOidcClientSecret")
        self.assertNotIn("KC_TARGET_", job)

    def test_librechat_oidc_client_deduplicates_equal_web_origins(self) -> None:
        duplicate_origin = self.temp_path / "duplicate-librechat-origin.yaml"
        duplicate_origin.write_text(
            """authKeycloak:
  librechatAdminWebOrigin: https://librechat.security.test
""",
            encoding="ascii",
        )
        manifest = run_helm(
            LIBRECHAT_OIDC_CHART,
            [*SHARED_VALUES, self.fixture, duplicate_origin],
            "mcp-security-librechat-oidc-duplicate-origin",
        )
        job = manifest_document(manifest, "Job", "auth-keycloak-librechat-oidc-job")

        self.assertRegex(
            job,
            r'name: KC_WEB_ORIGINS\n\s+value: "\[\\"https://librechat\.security\.test\\"\]"',
            job,
        )

    def test_audiences_and_langfuse_fields_remain_explicit(self) -> None:
        """Emit only verified user identity and prevent credential telemetry leaks."""
        auth_policy = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-auth-ag-policy",
        )
        studio_job = manifest_document(
            self.studio_manifest,
            "Job",
            "auth-keycloak-studio-oidc-job",
        )
        parameters = manifest_document(
            self.agentgateway_manifest,
            "AgentgatewayParameters",
            "infra-agentgateway-parameters",
        )
        self.assert_document_contains(
            auth_policy,
            'audiences: ["agentgateway","librechat"]',
        )
        self.assertNotIn('"*"', auth_policy, auth_policy)
        self.assert_document_contains(
            studio_job,
            '["realm-management", "keycloak-api-key-bridge"]',
        )
        self.assertNotIn('["realm-management", "agentgateway", "keycloak-api-key-bridge"]', studio_job)
        for attribute in (
            "langfuse.observation.metadata.mcp.operation",
            "langfuse.observation.metadata.mcp.server",
            "langfuse.observation.metadata.mcp.tool",
            "langfuse.observation.metadata.mcp.session_id",
            "langfuse.observation.input",
            "langfuse.observation.output",
        ):
            self.assert_document_contains(parameters, attribute)
        self.assert_document_contains(parameters, "flattenRecursive(mcp.tool.arguments)")
        self.assert_document_contains(parameters, "flattenRecursive(mcp.tool.result)")
        self.assert_document_contains(parameters, "langfuse.user.id")
        self.assertIn(
            'has(jwt.sub) ? jwt.sub : (has(extauthz.principal_id) ? extauthz.principal_id : "")',
            " ".join(parameters.split()),
            parameters,
        )
        for forbidden_metadata in (
            "extauthz.credential_id",
            "extauthz.credential_kind",
            "extauthz.credential_name",
            "extauthz.expires_at",
            "extauthz.permissions",
            "extauthz.principal_kind",
        ):
            self.assertNotIn(forbidden_metadata, parameters, parameters)
        self.assertNotIn("x-auth-user", parameters, parameters)
        self.assertNotIn("authorization", parameters.lower(), parameters)
        self.assertNotIn(self.synthetic_secret, parameters, parameters)


class InternalRagEmbeddingPolicyTests(unittest.TestCase):
    """Verify the dedicated RAG embeddings listener remains private and gated."""

    @classmethod
    def setUpClass(cls) -> None:
        """Render disabled and enabled AgentGateway configurations."""
        cls.temp_directory = tempfile.TemporaryDirectory(prefix="rag-embedding-security-")
        cls.temp_path = Path(cls.temp_directory.name)
        cls.disabled_fixture = cls.write_fixture("disabled.yaml", enabled=False)
        cls.enabled_fixture = cls.write_fixture("enabled.yaml", enabled=True)
        cls.disabled_manifest = run_helm(
            AGENTGATEWAY_CHART,
            [cls.disabled_fixture],
            "rag-embedding-disabled",
        )
        cls.enabled_manifest = run_helm(
            AGENTGATEWAY_CHART,
            [cls.enabled_fixture],
            "rag-embedding-enabled",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        """Delete generated values fixtures."""
        cls.temp_directory.cleanup()

    @classmethod
    def write_fixture(cls, filename: str, *, enabled: bool) -> Path:
        """Write a synthetic values file for the internal embeddings contract."""
        path = cls.temp_path / filename
        feature_values = ""
        if enabled:
            feature_values = """externalGateway:
  enabled: true
internalRagEmbedding:
  enabled: true
  listenerPort: 8081
  backend:
    host: embeddings.backend.test
    port: 8080
    model: rag/embed
"""
        path.write_text(
            f"""authKeycloak:
  hostname: auth.embedding.test
  realm: embedding
  agentgatewayClientRoles:
    - llm:invoke
    - model:rag/embed:invoke
infraAgentgatewayWrapper:
  hostname: agentgateway.embedding.test
{feature_values}""",
            encoding="ascii",
        )
        return path

    def test_disabled_by_default_and_missing_backend_values_fail_closed(self) -> None:
        """Render nothing by default and reject incomplete enabled contracts."""
        self.assertNotIn("internal-rag-embedding", self.disabled_manifest)
        enabled_values = self.enabled_fixture.read_text(encoding="ascii")
        invalid_replacements = (
            ("host: embeddings.backend.test", 'host: ""'),
            ("port: 8080", "port: 0"),
            ("model: rag/embed", 'model: ""'),
        )
        for index, (configured, missing) in enumerate(invalid_replacements):
            with self.subTest(field=configured.split(":", maxsplit=1)[0]):
                fixture = self.temp_path / f"missing-backend-{index}.yaml"
                fixture.write_text(enabled_values.replace(configured, missing), encoding="ascii")
                self.assertTrue(
                    helm_rejects(
                        AGENTGATEWAY_CHART,
                        [fixture],
                        f"rag-embedding-missing-{index}",
                    )
                )

    def test_listener_is_private_and_preserves_shared_listener(self) -> None:
        """Add only an internal model listener without changing websecure."""
        disabled_gateway = manifest_document(
            self.disabled_manifest,
            "Gateway",
            "infra-agentgateway-gateway",
        )
        enabled_gateway = manifest_document(
            self.enabled_manifest,
            "Gateway",
            "infra-agentgateway-gateway",
        )
        listener_pattern = re.compile(r"(?ms)^    - name: websecure\n.*?(?=^    - name:|\Z)")
        disabled_websecure = listener_pattern.search(disabled_gateway)
        enabled_websecure = listener_pattern.search(enabled_gateway)
        self.assertIsNotNone(disabled_websecure, disabled_gateway)
        self.assertIsNotNone(enabled_websecure, enabled_gateway)
        self.assertEqual(
            disabled_websecure.group(0).rstrip(),
            enabled_websecure.group(0).rstrip(),
        )

        internal_pattern = re.compile(r"(?ms)^    - name: internal-rag-embedding\n.*?(?=^    - name:|\Z)")
        internal_listener = internal_pattern.search(enabled_gateway)
        self.assertIsNotNone(internal_listener, enabled_gateway)
        internal_listener_text = internal_listener.group(0)
        self.assertIn("port: 8081", internal_listener_text)
        self.assertIn("kind: AgentgatewayModel", internal_listener_text)
        self.assertNotIn("kind: HTTPRoute", internal_listener_text)

        external_route = manifest_document(
            self.enabled_manifest,
            "HTTPRoute",
            "infra-agentgateway-external-httproute",
        )
        self.assertIn("port: 80", external_route)
        self.assertNotIn("8081", external_route)
        self.assertNotIn("internal-rag-embedding", external_route)
        self.assertEqual(
            len(re.findall(r"(?m)^kind:\s*HTTPRoute\s*$", self.enabled_manifest)),
            1,
            self.enabled_manifest,
        )

    def test_model_uses_embeddings_format_and_concrete_auth_gate(self) -> None:
        """Route only the configured model and require both normal auth gates."""
        model = manifest_document(
            self.enabled_manifest,
            "AgentgatewayModel",
            "infra-agentgateway-internal-rag-embedding-model",
        )
        gateway_auth = manifest_document(
            self.enabled_manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-auth-ag-policy",
        )
        credential_removal = manifest_document(
            self.enabled_manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-remove-caller-credentials",
        )
        self.assertIn("sectionName: internal-rag-embedding", model)
        self.assertIn('model: "rag/embed"', model)
        self.assertIn('baseURL: "http://embeddings.backend.test:8080"', model)
        self.assertIn("provider: Custom", model)
        self.assertIn("- type: Embeddings", model)
        self.assertIn("model:rag/embed:invoke", model)
        self.assertIn("jwt.resource_access.agentgateway.roles", model)
        self.assertIn("extauthz.permissions", model)
        self.assertIn("field: model", model)
        self.assertIn("expression: '\"rag/embed\"'", model)

        self.assertIn('"llm:invoke" in jwt.resource_access.agentgateway.roles', gateway_auth)
        self.assertIn('"llm:invoke" in extauthz.permissions', gateway_auth)
        self.assertIn("extAuth:", gateway_auth)
        self.assertNotIn("sectionName:", gateway_auth)
        self.assertIn("phase: PostRouting", credential_removal)
        self.assertIn("- Authorization", credential_removal)
        self.assertIn("- x-api-key", credential_removal)
        self.assertNotIn("sectionName:", credential_removal)

    def test_extproc_and_content_telemetry_are_excluded_from_listener(self) -> None:
        """Keep extProc on websecure and disable both telemetry content paths."""
        telemetry = manifest_document(
            self.enabled_manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-internal-rag-embedding-telemetry-policy",
        )
        disabled_sink = manifest_document(
            self.enabled_manifest,
            "AgentgatewayBackend",
            "infra-agentgateway-disabled-telemetry-backend",
        )
        self.assertNotIn("infra-agentgateway-policy-extproc", self.enabled_manifest)
        self.assertIn("sectionName: internal-rag-embedding", telemetry)
        self.assertIn('accessLog:\n      filter: "false"', telemetry)
        self.assertIn('randomSampling: "false"', telemetry)
        self.assertIn('clientSampling: "false"', telemetry)
        self.assertEqual(telemetry.count('filter: "false"'), 2, telemetry)
        self.assertIn("name: infra-agentgateway-disabled-telemetry-backend", telemetry)
        self.assertIn("host: telemetry-disabled.invalid", disabled_sink)

    def test_network_policy_exposes_dedicated_port_only_to_rag_api(self) -> None:
        """Allow the RAG API identity, and no other existing caller, on port 8081."""
        network_policy = manifest_document(
            self.enabled_manifest,
            "NetworkPolicy",
            "infra-agentgateway-data-plane-network-policy",
        )
        dedicated_ingress = """- namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: frontend-librechat
          podSelector:
            matchLabels:
              app.kubernetes.io/name: frontend-librechat-rag-api
              app.kubernetes.io/component: rag-api
      ports:
        - port: 8081
          protocol: TCP"""
        self.assertIn(dedicated_ingress, network_policy)
        self.assertEqual(network_policy.count("port: 8081"), 1, network_policy)
        self.assertEqual(
            network_policy.count("app.kubernetes.io/name: frontend-librechat-rag-api"),
            1,
            network_policy,
        )
        self.assertIn("port: 80", network_policy)
        self.assertNotIn("ipBlock:", network_policy)

    def test_data_plane_allows_dify_plugin_model_validation(self) -> None:
        """The plugin daemon calls the Gateway while validating model credentials."""
        network_policy = manifest_document(
            self.enabled_manifest,
            "NetworkPolicy",
            "infra-agentgateway-data-plane-network-policy",
        )
        self.assertIn(
            "kubernetes.io/metadata.name: frontend-dify\n"
            "          podSelector:\n"
            "            matchLabels:\n"
            "              app.kubernetes.io/name: plugin-daemon",
            network_policy,
        )


if __name__ == "__main__":
    unittest.main()
