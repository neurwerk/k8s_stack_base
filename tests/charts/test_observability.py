"""Rendered contracts for platform observability charts."""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"
RESOURCE_VALUES = ROOT / "releases/shared/resources.yaml"
DASHBOARD = ROOT / "charts/agentgateway/files/llm-user-experience-dashboard.json"


def render(chart: str, release: str, namespace: str, *set_values: str) -> str:
    """Render one chart with platform validation values and optional overrides."""
    command = [
        "helm",
        "template",
        release,
        str(ROOT / "charts" / chart),
        "--namespace",
        namespace,
        "--values",
        str(LINT_VALUES),
        "--values",
        str(RESOURCE_VALUES),
        "--skip-tests",
    ]
    for value in set_values:
        command.extend(("--set", value))
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def resource(manifest: str, kind: str, name: str) -> str:
    """Return exactly one rendered resource by kind and metadata name."""
    matches = [
        document
        for document in re.split(r"(?m)^---\s*$", manifest)
        if re.search(rf"(?m)^kind: {re.escape(kind)}$", document)
        and re.search(rf"(?m)^  name: {re.escape(name)}$", document)
    ]
    if len(matches) != 1:
        raise AssertionError(f"Expected one {kind} {name}, found {len(matches)}")
    return matches[0]


class ObservabilityChartTests(unittest.TestCase):
    """Protect platform-specific behavior layered over upstream charts."""

    def test_platform_release_enables_email_with_optional_openbao_values(self) -> None:
        defaults = (ROOT / "releases/kube-prometheus-stack/app-defaults.yaml").read_text(
            encoding="utf-8"
        )
        release = (ROOT / "releases/kube-prometheus-stack/app.yaml").read_text(
            encoding="utf-8"
        )

        self.assertRegex(
            defaults,
            r"(?s)monitorKubePrometheusStack:.*?alerting:.*?email:.*?enabled: true",
        )
        self.assertRegex(
            release,
            r"(?s)- kind: ConfigMap\n\s+name: kube-prometheus-stack-product-values"
            r".*?- kind: Secret\n\s+name: monitor-kube-prometheus-stack-smtp-secret"
            r"\n\s+valuesKey: values.yaml\n\s+optional: true",
        )

    def test_fluent_bit_accepts_large_kubernetes_metadata_responses(self) -> None:
        manifest = render("fluent-bit", "monitor-fluent-bit", "monitor-fluent-bit")

        self.assertIn("Buffer_Size         128k", manifest)

    def test_langfuse_disables_only_clickhouse_internal_diagnostic_history(self) -> None:
        manifest = render("langfuse", "monitor-langfuse", "monitor-langfuse")

        for table in (
            "trace_log",
            "text_log",
            "metric_log",
            "asynchronous_metric_log",
            "latency_log",
        ):
            with self.subTest(table=table):
                self.assertIn(f'<{table} remove="remove"/>', manifest)

        self.assertNotIn("CLICKHOUSE_SETTINGS_MAX_MEMORY_USAGE", manifest)
        self.assertNotIn(
            "CLICKHOUSE_SETTINGS_MAX_BYTES_TO_MERGE_AT_MAX_SPACE_IN_POOL",
            manifest,
        )
        self.assertIn("monitor-langfuse-clickhouse", manifest)

    def test_langfuse_uses_external_operations_postgresql(self) -> None:
        manifest = render("langfuse", "monitor-langfuse", "monitor-langfuse")
        database_env = (
            r'(?s)- name: DATABASE_HOST\s+value: '
            r'"postgres-operations\.infra-postgres-operations\.svc\.cluster\.local"'
            r'.*?- name: DATABASE_PORT\s+value: "5432"'
            r'.*?- name: DATABASE_USERNAME\s+value: "langfuse"'
            r'.*?- name: DATABASE_PASSWORD\s+valueFrom:\s+secretKeyRef:'
            r'\s+name: monitor-langfuse-secret\s+key: postgresql-password'
            r'.*?- name: DATABASE_NAME\s+value: "postgres_langfuse"'
            r'.*?- name: DATABASE_ARGS\s+value: "sslmode=disable"'
        )

        for component in ("web", "worker"):
            with self.subTest(component=component):
                deployment = resource(
                    manifest, "Deployment", f"monitor-langfuse-{component}"
                )
                self.assertRegex(deployment, database_env)

        self.assertNotIn("name: monitor-langfuse-postgresql\n", manifest)
        self.assertIn("name: monitor-langfuse-clickhouse", manifest)
        self.assertIn("name: monitor-langfuse-redis-primary", manifest)
        self.assertIn(
            "name: monitor-langfuse-langfuse-object-bucket-claim", manifest
        )

    def test_langfuse_postgresql_egress_is_exact(self) -> None:
        manifest = render("langfuse", "monitor-langfuse", "monitor-langfuse")
        application = resource(
            manifest, "NetworkPolicy", "monitor-langfuse-postgresql-egress"
        )
        retention = resource(
            manifest,
            "NetworkPolicy",
            "monitor-langfuse-retention-postgresql-egress",
        )
        retention_job = resource(
            manifest, "Job", "monitor-langfuse-init-retention-job"
        )

        self.assertIn(
            "operator: In\n        values:\n          - web\n          - worker",
            application,
        )
        self.assertIn(
            "app.kubernetes.io/name: langfuse-retention-provisioning",
            retention,
        )
        for policy in (application, retention):
            with self.subTest(policy=policy.splitlines()[0:8]):
                self.assertIn(
                    "kubernetes.io/metadata.name: infra-postgres-operations", policy
                )
                self.assertIn("app.kubernetes.io/name: postgres-operations", policy)
                self.assertIn("app.kubernetes.io/instance: postgres-operations", policy)
                self.assertIn("port: 9712", policy)
                self.assertIn("kubernetes.io/metadata.name: kube-system", policy)
                self.assertEqual(policy.count("port: 53"), 2)

        for destination, port in (
            ("app.kubernetes.io/name: clickhouse", 8123),
            ("app.kubernetes.io/name: clickhouse", 9000),
            ("app.kubernetes.io/name: redis", 6379),
            ("rook_object_store: infra-rook-ceph-object-store", 8080),
        ):
            with self.subTest(destination=destination, port=port):
                self.assertIn(destination, application)
                self.assertIn(f"port: {port}", application)

        self.assertRegex(
            retention_job,
            r'(?s)- name: PGHOST\s+value: '
            r'postgres-operations\.infra-postgres-operations\.svc\.cluster\.local'
            r'.*?- name: PGPORT\s+value: "5432"'
            r'.*?- name: PGUSER\s+value: langfuse'
            r'.*?key: postgresql-password'
            r'.*?- name: PGDATABASE\s+value: postgres_langfuse'
            r'.*?- name: PGSSLMODE\s+value: disable',
        )

    def test_langfuse_release_depends_on_operations_postgresql(self) -> None:
        release = (ROOT / "releases/langfuse/app.yaml").read_text(encoding="ascii")

        self.assertRegex(
            release,
            r"(?s)dependsOn:.*?- name: postgres-operations"
            r"\s+namespace: infra-postgres-operations",
        )

    def test_k3s_overrides_remove_only_unsupported_component_monitors(self) -> None:
        manifest = render(
            "kube-prometheus-stack",
            "monitor-kube-prometheus-stack",
            "monitor-kube-prometheus-stack",
            "kube-prometheus-stack.kubeControllerManager.enabled=false",
            "kube-prometheus-stack.kubeScheduler.enabled=false",
            "kube-prometheus-stack.kubeProxy.enabled=false",
        )

        for alert in (
            "KubeControllerManagerDown",
            "KubeSchedulerDown",
            "KubeProxyDown",
        ):
            with self.subTest(alert=alert):
                self.assertNotIn(alert, manifest)

        self.assertIn("kube-state-metrics", manifest)
        self.assertIn("prometheus-node-exporter", manifest)

    def test_grafana_auto_discovers_gitops_dashboards(self) -> None:
        manifest = render(
            "kube-prometheus-stack",
            "monitor-kube-prometheus-stack",
            "monitor-kube-prometheus-stack",
        )
        grafana = resource(
            manifest,
            "Deployment",
            "monitor-kube-prometheus-stack-grafana",
        )
        prometheus = resource(
            manifest,
            "Prometheus",
            "monitor-kube-prometheus-st",
        )
        grafana_reader = resource(
            manifest,
            "Role",
            "monitor-kube-prometheus-stack-grafana-configmap-reader",
        )

        self.assertIn("name: grafana-sc-dashboard", grafana)
        self.assertRegex(
            grafana,
            r'(?s)name: grafana-sc-dashboard.*?name: LABEL\s+value: "grafana_dashboard"'
            r'.*?name: LABEL_VALUE\s+value: "1".*?name: RESOURCE\s+value: "configmap"'
            r'.*?name: NAMESPACE\s+'
            r'value: "monitor-kube-prometheus-stack,infra-agentgateway"',
        )
        self.assertIn(
            "http://localhost:3000/api/admin/provisioning/dashboards/reload",
            grafana,
        )
        self.assertEqual(grafana.count('value: "configmap"'), 2)
        self.assertIn("- configmaps", grafana_reader)
        self.assertNotIn("secrets", grafana_reader)
        self.assertNotIn(
            "monitor-kube-prometheus-stack-grafana-clusterrole",
            manifest,
        )
        for selector in (
            "serviceMonitorSelector: {}",
            "serviceMonitorNamespaceSelector: {}",
            "podMonitorSelector: {}",
            "podMonitorNamespaceSelector: {}",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, prometheus)

    def test_alertmanager_emails_only_critical_alerts_with_secret_backed_smtp(self) -> None:
        manifest = render(
            "kube-prometheus-stack",
            "monitor-kube-prometheus-stack",
            "monitor-kube-prometheus-stack",
        )
        alertmanager = resource(
            manifest,
            "Alertmanager",
            "monitor-kube-prometheus-st",
        )
        config = resource(
            manifest,
            "Secret",
            "monitor-kube-prometheus-stack-alertmanager-config",
        )
        egress = resource(
            manifest,
            "NetworkPolicy",
            "monitor-kube-prometheus-stack-alertmanager-egress",
        )

        self.assertIn(
            "configSecret: monitor-kube-prometheus-stack-alertmanager-config",
            alertmanager,
        )
        self.assertIn("alerts.neurwerk.com/managed-by: monitor-kube-prometheus-stack", alertmanager)
        self.assertNotIn("alertmanagerConfigSelector: {}", alertmanager)
        self.assertIn('smtp_smarthost: "smtp.example.test:465"', config)
        self.assertIn('smtp_from: "no-reply@lint.example"', config)
        self.assertIn('smtp_auth_username: "lint-smtp-user"', config)
        self.assertIn('smtp_auth_password: "lint-smtp-password"', config)
        self.assertIn("smtp_require_tls: false", config)
        self.assertIn("- severity = critical", config)
        self.assertNotIn("severity = warning\n          receiver: critical-email", config)
        self.assertIn("send_resolved: true", config)
        self.assertIn("- alertname = Watchdog", config)
        self.assertIn("port: 465", egress)
        self.assertIn("port: 53", egress)
        self.assertIn("port: 9094", egress)
        self.assertIn("cidr: 0.0.0.0/0", egress)
        self.assertIn("- 169.254.0.0/16", egress)
        self.assertIn("- 198.51.100.0/24", egress)

    def test_disabled_email_retains_alertmanager_egress_isolation(self) -> None:
        manifest = render(
            "kube-prometheus-stack",
            "monitor-kube-prometheus-stack",
            "monitor-kube-prometheus-stack",
            "monitorKubePrometheusStack.alerting.email.enabled=false",
        )
        config = resource(
            manifest,
            "Secret",
            "monitor-kube-prometheus-stack-alertmanager-config",
        )
        egress = resource(
            manifest,
            "NetworkPolicy",
            "monitor-kube-prometheus-stack-alertmanager-egress",
        )

        self.assertNotIn("smtp_smarthost", config)
        self.assertNotIn("critical-email", config)
        self.assertIn("port: 53", egress)
        self.assertIn("port: 9094", egress)
        self.assertNotIn("port: 465", egress)

    def test_agentgateway_monitoring_resources_are_enabled(self) -> None:
        manifest = render("agentgateway", "infra-agentgateway", "infra-agentgateway")

        pod_monitor = resource(manifest, "PodMonitor", "infra-agentgateway-proxy")
        service_monitor = resource(manifest, "ServiceMonitor", "infra-agentgateway")
        upstream_dashboard = resource(manifest, "ConfigMap", "infra-agentgateway-dashboard")
        ux_dashboard = resource(
            manifest,
            "ConfigMap",
            "infra-agentgateway-llm-user-experience-dashboard",
        )
        dashboard_reader = resource(
            manifest,
            "Role",
            "infra-agentgateway-grafana-dashboard-reader",
        )
        dashboard_reader_binding = resource(
            manifest,
            "RoleBinding",
            "infra-agentgateway-grafana-dashboard-reader",
        )

        for monitor in (pod_monitor, service_monitor):
            self.assertIn("interval: 30s", monitor)
            self.assertIn("release: monitor-kube-prometheus-stack", monitor)
        self.assertIn('grafana_dashboard: "1"', upstream_dashboard)
        self.assertIn('grafana_dashboard: "1"', ux_dashboard)
        self.assertIn('"uid": "llm-user-experience"', ux_dashboard)
        self.assertIn("- configmaps", dashboard_reader)
        self.assertNotIn("secrets", dashboard_reader)
        self.assertIn(
            "name: monitor-kube-prometheus-stack-grafana",
            dashboard_reader_binding,
        )
        self.assertIn(
            "namespace: monitor-kube-prometheus-stack",
            dashboard_reader_binding,
        )

    def test_agentgateway_frontend_buffer_requires_policy_engine(self) -> None:
        manifest = render("agentgateway", "infra-agentgateway", "infra-agentgateway")
        parameters = resource(
            manifest,
            "AgentgatewayParameters",
            "infra-agentgateway-parameters",
        )
        without_policy = render(
            "agentgateway",
            "infra-agentgateway",
            "infra-agentgateway",
            "guardrails.llmPolicyEngine.enabled=false",
            "guardrails.llmPolicyEngine.models=null",
        )
        parameters_without_policy = resource(
            without_policy,
            "AgentgatewayParameters",
            "infra-agentgateway-parameters",
        )

        self.assertRegex(
            parameters,
            r"(?s)rawConfig:\s+frontendPolicies:\s+http:\s+maxBufferSize: 6291456"
            r"\s+config:\s+tracing:",
        )
        self.assertNotIn("frontendPolicies:", parameters_without_policy)
        self.assertIn("config:\n      tracing:", parameters_without_policy)

    def test_agentgateway_content_fields_use_destination_policy(self) -> None:
        """Render omission defaults as enabled while retaining explicit null branches."""
        manifest = render("agentgateway", "infra-agentgateway", "infra-agentgateway")
        parameters = resource(
            manifest,
            "AgentgatewayParameters",
            "infra-agentgateway-parameters",
        )
        normalized = " ".join(parameters.split())

        self.assertIn("has(metadata.agentgateway_user_model)", normalized)
        self.assertIn("metadata.agentgateway_user_model in []", normalized)
        self.assertIn("has(mcp.tool.target)", normalized)
        self.assertIn("mcp.tool.target in []", normalized)
        self.assertEqual(parameters.count(": null'"), 4, parameters)
        self.assertNotIn("promptTracingEnabled", parameters)

    def test_agentgateway_ux_metrics_are_bounded_and_scrapeable(self) -> None:
        manifest = render("agentgateway", "infra-agentgateway", "infra-agentgateway")
        policy = resource(
            manifest,
            "AgentgatewayPolicy",
            "infra-agentgateway-ux-metrics",
        )
        network_policy = resource(
            manifest,
            "NetworkPolicy",
            "infra-agentgateway-data-plane-network-policy",
        )
        controller_network_policy = resource(
            manifest,
            "NetworkPolicy",
            "infra-agentgateway-controller-network-policy",
        )

        self.assertIn("name: request_size", policy)
        self.assertIn("name: llm_streaming", policy)
        self.assertIn("size(request.body)", policy)
        self.assertNotIn("bytes(request.body)", policy)
        for bucket in (
            "0-4KiB",
            "4-16KiB",
            "16-64KiB",
            "64-256KiB",
            "256KiB-1MiB",
            "1-5MiB",
            "5MiB+",
            "unknown",
        ):
            with self.subTest(bucket=bucket):
                self.assertIn(f'\"{bucket}\"', policy)
        self.assertIn(
            'size(request.body) <= 5242880 ? "1-5MiB" : "5MiB+"',
            policy,
        )
        self.assertNotIn('"1MiB+"', policy)
        for forbidden_label in ("user_id", "request_id", "session_id", "conversation_id"):
            with self.subTest(label=forbidden_label):
                self.assertNotIn(f"name: {forbidden_label}", policy)

        prometheus_source = (
            r"(?s)kubernetes.io/metadata.name: monitor-kube-prometheus-stack"
            r".*?podSelector:.*?app.kubernetes.io/name: prometheus"
            r".*?operator.prometheus.io/name: monitor-kube-prometheus-st"
        )
        for policy in (network_policy, controller_network_policy):
            with self.subTest(policy=policy):
                self.assertRegex(policy, prometheus_source)
                self.assertIn("port: metrics", policy)
        self.assertIn("agentgateway: agentgateway", controller_network_policy)
        self.assertIn("port: grpc-xds-agw", controller_network_policy)
        self.assertIn(
            "gateway.networking.k8s.io/gateway-name: infra-agentgateway-gateway",
            controller_network_policy,
        )

    def test_llm_user_experience_dashboard_uses_latency_contract(self) -> None:
        dashboard = json.loads(DASHBOARD.read_text(encoding="ascii"))
        panels = {panel["title"]: panel for panel in dashboard["panels"]}

        for title in (
            "Time to First Token by Request Size",
            "LLM Response Duration by Request Size",
            "Time per Output Token by Request Size",
            "Percentile Observation Counts",
            "PII Decision Round-Trip",
            "PII Analysis and Queue Time",
            "Parsed LLM Response Metric Coverage",
            "LLM Histogram Overflow",
            "AgentGateway Controller Reconciliation",
            "AgentGateway xDS Health",
        ):
            with self.subTest(panel=title):
                self.assertIn(title, panels)

        queries = "\n".join(
            target["expr"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
        )
        for metric in (
            "agentgateway_gen_ai_server_time_to_first_token_bucket",
            "agentgateway_gen_ai_server_time_per_output_token_bucket",
            "agentgateway_gen_ai_server_request_duration_bucket",
            "extproc_engine_request_latency_seconds_bucket",
            "pii_engine_analysis_duration_seconds_bucket",
            "pii_engine_queue_wait_seconds_bucket",
            "agentgateway_controller_reconciliations_total",
            "agentgateway_controller_reconcile_duration_seconds_bucket",
            "agentgateway_xds_auth_rq_failure_total",
            "agentgateway_xds_rejects_total",
        ):
            with self.subTest(metric=metric):
                self.assertIn(metric, queries)
        self.assertIn("histogram_quantile(0.50", queries)
        self.assertIn("histogram_quantile(0.95", queries)
        self.assertIn('request_size=~"${request_size:regex}"', queries)
        self.assertIn('llm_streaming=~"${streaming:regex}"', queries)
        self.assertIn("increase(agentgateway_gen_ai_server_request_duration_count", queries)
        self.assertIn("pii_engine_queue_wait_seconds_count", queries)
        self.assertIn('le=\"+Inf\"', queries)
        self.assertIn('le=\"81.92\"', queries)
        self.assertIn('le=\"10\"', queries)
        self.assertIn('le=\"2.5\"', queries)
        self.assertIn(
            "only success is a validated decision",
            panels["PII Decision Round-Trip"]["description"],
        )
        self.assertIn(
            "Parsed LLM responses only",
            panels["LLM Response Duration by Request Size"]["description"],
        )
        token_counts = next(
            target
            for target in panels["Percentile Observation Counts"]["targets"]
            if target["refId"] == "D"
        )
        self.assertIn(
            "sum by (gen_ai_request_model, gen_ai_token_type)",
            token_counts["expr"],
        )
        self.assertEqual(
            token_counts["legendFormat"],
            "tokens {{gen_ai_request_model}} {{gen_ai_token_type}}",
        )
        self.assertNotIn("[$__range]", queries)

    def test_llm_dashboard_supports_namespace_qualified_routes(self) -> None:
        dashboard = json.loads(DASHBOARD.read_text(encoding="ascii"))
        route_targets = [
            (panel["title"], target["expr"])
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
            if "route=~" in target["expr"]
        ]

        self.assertEqual(
            {title for title, _ in route_targets},
            {
                "Request Rate by Status and Reason",
                "Time to First Token by Request Size",
                "LLM Response Duration by Request Size",
                "Time per Output Token by Request Size",
                "Input and Output Tokens per Request",
                "Percentile Observation Counts",
                "Parsed LLM Response Metric Coverage",
                "LLM Histogram Overflow",
            },
        )
        for title, query in route_targets:
            with self.subTest(panel=title):
                self.assertIn('route=~"${route}"', query)
                self.assertNotIn("${route:regex}", query)

        route_variable = next(
            variable
            for variable in dashboard["templating"]["list"]
            if variable["name"] == "route"
        )
        self.assertIs(route_variable["multi"], True)
        self.assertIs(route_variable["includeAll"], True)
        self.assertEqual(
            route_variable["query"],
            "label_values(agentgateway_requests_total,route)",
        )


if __name__ == "__main__":
    unittest.main()
