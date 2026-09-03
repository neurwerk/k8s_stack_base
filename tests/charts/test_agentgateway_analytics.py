"""Rendered contracts for AgentGateway request-log usage analytics."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / "tests/validation/helm-lint-values.yaml"


def render(chart: str, release: str, namespace: str, *extra_args: str) -> str:
    result = subprocess.run(
        [
            "helm",
            "template",
            release,
            str(ROOT / chart),
            "--namespace",
            namespace,
            "--values",
            str(VALUES),
            *extra_args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def documents(manifest: str) -> list[str]:
    return [
        document
        for document in re.split(r"(?m)^---\s*$", manifest)
        if document.strip()
    ]


def resource(manifest: str, kind: str, name: str) -> str:
    matches = [
        document
        for document in documents(manifest)
        if re.search(rf"(?m)^kind:\s*{re.escape(kind)}\s*$", document)
        and re.search(rf"(?m)^  name:\s*{re.escape(name)}\s*$", document)
    ]
    if len(matches) != 1:
        raise AssertionError(f"Expected one {kind} {name}, found {len(matches)}")
    return matches[0]


class AgentGatewayAnalyticsTests(unittest.TestCase):
    def test_agentgateway_renders_private_metadata_only_database_logging(self) -> None:
        manifest = render(
            "charts/agentgateway",
            "infra-agentgateway",
            "infra-agentgateway",
        )
        parameters = resource(
            manifest, "AgentgatewayParameters", "infra-agentgateway-parameters"
        )

        self.assertIn(
            """  env:
    - name: AGENTGATEWAY_DATABASE_PASSWORD
      valueFrom:
        secretKeyRef:
          name: infra-agentgateway-database-secret
          key: password""",
            parameters,
        )
        self.assertIn(
            "secret.reloader.stakater.com/reload: "
            "infra-agentgateway-database-secret",
            parameters,
        )
        self.assertIn(
            """        - name: admin
          port: 15000
          targetPort: 15000
          protocol: TCP""",
            parameters,
        )
        self.assertIn(
            """      accessLog:
        database:
          llm: metadata""",
            parameters,
        )
        self.assertIn('adminAddr: "0.0.0.0:15000"', parameters)
        self.assertIn("has(jwt.sub)", parameters)
        self.assertIn("has(extauthz.principal_id)", parameters)
        self.assertIn("logging:\n        database:", parameters)
        self.assertIn("${AGENTGATEWAY_DATABASE_PASSWORD}", parameters)
        self.assertIn("maxConnections: 5", parameters)
        self.assertNotIn("lint-agentgateway-database-password", parameters)

        non_secrets = "\n---\n".join(
            document
            for document in documents(manifest)
            if not re.search(r"(?m)^kind:\s*Secret\s*$", document)
        )
        self.assertNotIn("lint-agentgateway-database-password", non_secrets)

        policy = resource(
            manifest,
            "NetworkPolicy",
            "infra-agentgateway-data-plane-network-policy",
        )
        self.assertIn(
            """    - from:
        # The admin listener is unauthenticated in AgentGateway 1.5.0. Only the
        # authenticated Studio API may reach it; it is never publicly routed.
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: frontend-studio
          podSelector:
            matchLabels:
              app.kubernetes.io/name: studio-api
              app.kubernetes.io/instance: frontend-studio-api
      ports:
        - port: 15000
          protocol: TCP""",
            policy,
        )
        self.assertIn("policyTypes:\n    - Ingress", policy)
        self.assertNotIn("- Egress", policy)

        for document in documents(manifest):
            if re.search(r"(?m)^kind:\s*(Gateway|HTTPRoute)\s*$", document):
                self.assertNotIn("15000", document)

    def test_database_logging_does_not_depend_on_guardrails_or_tracing(self) -> None:
        manifest = render(
            "charts/agentgateway",
            "infra-agentgateway",
            "infra-agentgateway",
            "--set",
            "guardrails.llmPolicyEngine.enabled=false",
            "--set-json",
            "guardrails.llmPolicyEngine.models=[]",
            "--set-json",
            "infraAgentgatewayWrapper.tracing=null",
        )
        parameters = resource(
            manifest, "AgentgatewayParameters", "infra-agentgateway-parameters"
        )

        self.assertNotIn("maxBufferSize", parameters)
        self.assertNotIn("tracing:", parameters)
        self.assertIn("logging:\n        database:", parameters)

    def test_postgres_and_studio_network_contracts_and_release_order(self) -> None:
        postgres = render(
            "charts/postgres/operations",
            "postgres-operations",
            "infra-postgres-operations",
        )
        postgres_policy = resource(
            postgres, "NetworkPolicy", "postgres-operations-ingress"
        )
        self.assertIn(
            """        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: infra-agentgateway
          podSelector:
            matchLabels:
              app.kubernetes.io/name: infra-agentgateway-gateway
              gateway.networking.k8s.io/gateway-name: infra-agentgateway-gateway""",
            postgres_policy,
        )
        self.assertIn("- port: 9712\n          protocol: TCP", postgres_policy)

        studio = render(
            "charts/studio/api", "frontend-studio-api", "frontend-studio"
        )
        deployment = resource(studio, "Deployment", "frontend-studio-api-deployment")
        self.assertIn(
            """            - name: K8S_STUDIO_AGENTGATEWAY_ADMIN_URL
              value: "http://infra-agentgateway-gateway.infra-agentgateway.svc.cluster.local:15000"
            - name: K8S_STUDIO_USAGE_TIMEZONE
              value: "UTC""",
            deployment,
        )
        self.assertNotIn("LANGFUSE", deployment)

        studio_policy = resource(
            studio, "NetworkPolicy", "frontend-studio-api-egress-network-policy"
        )
        self.assertIn(
            """              kubernetes.io/metadata.name: infra-agentgateway
          podSelector:
            matchLabels:
              app.kubernetes.io/name: infra-agentgateway-gateway
              gateway.networking.k8s.io/gateway-name: infra-agentgateway-gateway
      ports:
        - port: 15000
          protocol: TCP""",
            studio_policy,
        )
        self.assertNotIn("monitor-langfuse", studio_policy)

        agentgateway_release = (ROOT / "releases/agentgateway/app.yaml").read_text(
            encoding="ascii"
        )
        studio_release = (ROOT / "releases/studio/api.yaml").read_text(
            encoding="ascii"
        )
        self.assertIn(
            """    - name: postgres-operations
      namespace: infra-postgres-operations""",
            agentgateway_release,
        )
        self.assertIn(
            """    - name: agentgateway
      namespace: infra-agentgateway""",
            studio_release,
        )
        self.assertNotIn("langfuse", studio_release.lower())

        studio_secret_sync = (
            ROOT / "releases/openbao/secret-sync/frontend-studio.yaml"
        ).read_text(encoding="ascii")
        self.assertNotIn("LANGFUSE_PUBLIC_KEY", studio_secret_sync)
        self.assertNotIn("LANGFUSE_SECRET_KEY", studio_secret_sync)

        agentgateway_secret_sync = (
            ROOT / "releases/openbao/secret-sync/infra-agentgateway.yaml"
        ).read_text(encoding="ascii")
        postgres_secret_sync = (
            ROOT / "releases/openbao/secret-sync/infra-postgres-operations.yaml"
        ).read_text(encoding="ascii")
        self.assertIn("property: postgresqlPassword", agentgateway_secret_sync)
        self.assertIn("databasePassword:", agentgateway_secret_sync)
        self.assertIn("property: agentgatewayPassword", postgres_secret_sync)
        self.assertIn("agentgatewayPassword:", postgres_secret_sync)


if __name__ == "__main__":
    unittest.main()
