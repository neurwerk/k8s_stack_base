"""Cross-chart scrape discovery and executable inspection renewal fault cases."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

from helm import documents, render, resource


CHART = "agentgateway/inspection-pki"
ROOT_IDS = ("r1", "next-3")
VALUES = {"agentgateway": {"inspection": {
    "enabled": True, "rootIds": list(ROOT_IDS), "monitoring": {"enabled": True},
}}}
SOURCE = {
    "job": "cert-manager", "namespace": "infra-cert-manager",
    "service": "infra-cert-manager-controller", "endpoint": "tcp-prometheus-servicemonitor",
}
METRICS = {
    "ready": "certmanager_certificate_ready_status",
    "renewal": "certmanager_certificate_renewal_timestamp_seconds",
    "expiry": "certmanager_certificate_expiration_timestamp_seconds",
}
ALERTS = (
    "CertificateSourceUnavailable", "CertificateMetricsMissing", "CertificateNotReady",
    "CertificateRenewalOverdue", "CertificateExpiringSoon", "RootRolloverDue",
)
PROMTOOL = os.environ.get("PROMTOOL") or shutil.which("promtool")


def selector_matches(selector, labels):
    if any(labels.get(k) != v for k, v in selector.get("matchLabels", {}).items()):
        return False
    for item in selector.get("matchExpressions", []):
        key, op, values = item["key"], item["operator"], item.get("values", [])
        if not {"In": labels.get(key) in values, "NotIn": labels.get(key) not in values,
                "Exists": key in labels, "DoesNotExist": key not in labels}[op]:
            return False
    return True


def series(metric, labels, values):
    return {"series": metric + "{" + ",".join(
        f"{key}={json.dumps(value)}" for key, value in sorted(labels.items())
    ) + "}", "values": values}


def certificate_series(root_id, role, metric, values):
    labels = {**SOURCE, "instance": "controller:9402",
              "exported_namespace": "infra-cert-manager" if role == "root" else "infra-agentgateway",
              "name": f"agentgateway-inspection-{role}-{root_id}-certificate",
              "issuer_name": "agentgateway-inspection-bootstrap-issuer" if role == "root"
              else f"agentgateway-inspection-root-{root_id}-issuer",
              "issuer_kind": "Issuer" if role == "root" else "ClusterIssuer",
              "issuer_group": "cert-manager.io"}
    if metric == "ready":
        labels["condition"] = "True"
    return series(METRICS[metric], labels, values)


def samples(overrides=None, *, source="1+0x180", roots=ROOT_IDS):
    data = [] if source is None else [series("up", {**SOURCE, "instance": "controller:9402"}, source)]
    for root_id in roots:
        for role in ("root", "signer"):
            days = (9 * 365, 10 * 365) if role == "root" else (60, 90)
            for metric, value in zip(METRICS, (1, days[0] * 86400, days[1] * 86400)):
                values = (overrides or {}).get((root_id, role, metric), f"{value}+0x180")
                if values is not None:
                    data.append(certificate_series(root_id, role, metric, values))
    return data


def expectations(at, **firing):
    """Check all alert names, including absence of unrelated alerts and generations."""
    return [{
        "expr": 'sum by (root_id, certificate_role) (ALERTS{alertstate="firing",'
                f'alertname="Inspection{alert}"}})',
        "eval_time": at,
        "exp_samples": [{
            "labels": "{}" if cert is None else
            f'{{root_id="{cert[0]}",certificate_role="{cert[1]}"}}',
            "value": 1,
        } for cert in firing.get(alert, ())],
    } for alert in ALERTS]


class InspectionMonitoringTests(unittest.TestCase):
    def check_rule_tests(self, rules, tests):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "rules.yaml").write_text(yaml.safe_dump(rules))
            (path / "test.yaml").write_text(yaml.safe_dump({
                "rule_files": ["rules.yaml"], "evaluation_interval": "30s", "tests": tests,
            }))
            for args in (("check", "rules", "rules.yaml"), ("test", "rules", "test.yaml")):
                result = subprocess.run([PROMTOOL, *args], cwd=path, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(f"promtool: {len(tests)} scenarios passed: " + "; ".join(t["name"] for t in tests))

    def test_opt_in_preserves_pki_and_defaults_have_no_alerts(self):
        self.assertEqual(documents(render(CHART, value_files=())), [])
        disabled = {"agentgateway": {"inspection": {
            "enabled": False, "monitoring": {"enabled": True},
        }}}
        self.assertEqual(documents(render(CHART, disabled, value_files=())), [])
        enabled = documents(render(CHART, VALUES, value_files=()))
        without_rules = render(CHART, {"agentgateway": {"inspection": {
            **VALUES["agentgateway"]["inspection"], "monitoring": {"enabled": False},
        }}}, value_files=())
        self.assertEqual([d for d in enabled if d["kind"] not in ("ServiceMonitor", "PrometheusRule")],
                         documents(without_rules))
        invalid = render(CHART, {"agentgateway": {"inspection": {
            "monitoring": {"enabled": "false"},
        }}}, check=False, value_files=())
        self.assertNotEqual(invalid.returncode, 0)

    def test_controller_scrape_discovery_and_metrics_only_peer(self):
        controller = render("cert-manager/controller", namespace="infra-cert-manager",
                            release="infra-cert-manager-controller", value_files=())
        self.assertFalse(any(d["apiVersion"].startswith("monitoring.coreos.com/")
                             for d in documents(controller)))
        pki = render(CHART, VALUES, value_files=())
        monitor = resource(pki, "ServiceMonitor")
        services = [d for d in documents(controller) if d["kind"] == "Service"
                    and selector_matches(monitor["spec"]["selector"], d["metadata"]["labels"])]
        self.assertEqual(len(services), 1)
        service = services[0]
        endpoint = monitor["spec"]["endpoints"][0]
        self.assertEqual(SOURCE, {
            "job": service["metadata"]["labels"][monitor["spec"]["jobLabel"]],
            "namespace": service["metadata"]["namespace"],
            "service": service["metadata"]["name"], "endpoint": endpoint["port"],
        })
        self.assertFalse(endpoint["honorLabels"])
        self.assertEqual(monitor["spec"]["namespaceSelector"]["matchNames"], [SOURCE["namespace"]])
        deployment = resource(controller, "Deployment", service["metadata"]["name"])
        pod = deployment["spec"]["template"]
        self.assertTrue(selector_matches({"matchLabels": service["spec"]["selector"]}, pod["metadata"]["labels"]))
        port = next(p for p in service["spec"]["ports"] if p["name"] == endpoint["port"])
        container_port = next(p["containerPort"] for p in pod["spec"]["containers"][0]["ports"]
                              if p["name"] == port["targetPort"])
        network = resource(controller, "NetworkPolicy")["spec"]
        self.assertTrue(selector_matches(network["podSelector"], pod["metadata"]["labels"]))
        self.assertEqual(network["policyTypes"], ["Ingress"])
        self.assertEqual(len(network["ingress"]), 1)
        self.assertEqual(network["ingress"][0]["ports"], [{"port": container_port, "protocol": "TCP"}])
        self.assertEqual(container_port, 9402)
        self.assertEqual(len(network["ingress"][0]["from"]), 1)
        peer = network["ingress"][0]["from"][0]

        monitoring = render("kube-prometheus-stack", namespace="monitor-kube-prometheus-stack",
                            release="monitor-kube-prometheus-stack", value_files=())
        prometheus = resource(monitoring, "Prometheus")
        serving = resource(monitoring, "Service", "monitor-kube-prometheus-st-prometheus")
        self.assertTrue(selector_matches(peer["podSelector"], serving["spec"]["selector"]))
        self.assertFalse(selector_matches(peer["podSelector"], {"app.kubernetes.io/name": "grafana"}))
        self.assertTrue(selector_matches(peer["namespaceSelector"], {
            "kubernetes.io/metadata.name": prometheus["metadata"]["namespace"],
        }))
        self.assertFalse(selector_matches(peer["namespaceSelector"], {"kubernetes.io/metadata.name": "other"}))
        rule = resource(pki, "PrometheusRule")
        for kind, obj in (("serviceMonitor", monitor), ("rule", rule)):
            self.assertTrue(selector_matches(prometheus["spec"][kind + "Selector"], obj["metadata"]["labels"]))
            self.assertTrue(selector_matches(prometheus["spec"][kind + "NamespaceSelector"], {
                "kubernetes.io/metadata.name": obj["metadata"]["namespace"],
            }))

    @unittest.skipUnless(PROMTOOL, "promtool unavailable; supply an existing binary via PROMTOOL")
    def test_rendered_rules_with_renewal_and_telemetry_faults(self):
        rules = resource(render(CHART, VALUES, value_files=()), "PrometheusRule")["spec"]
        tests = []

        def case(name, data, *checks):
            tests.append({"name": name, "interval": "1m", "input_series": data,
                          "promql_expr_test": [c for check in checks for c in check]})

        unselected = [certificate_series("old", "root", metric, values) for metric, values in (
            ("ready", "0+0x180"), ("renewal", "600+0x180"), ("expiry", "600+0x180"),
        )]
        case("healthy certificates and an unselected broken generation", samples() + unselected,
             expectations("3h"))
        late = samples({("r1", "signer", "renewal"): "600+0x89 5184000+0x90"})
        case("Ready stays true during late renewal; grace, hold and recovery", late,
             expectations("70m"), expectations("80m"),
             expectations("81m", CertificateRenewalOverdue=[("r1", "signer")]), expectations("91m"))
        case("one timestamp family disappears while other certificates remain", samples({
            ("next-3", "root", "expiry"): None,
        }), expectations("9m"), expectations("10m", CertificateMetricsMissing=[("next-3", "root")]))
        case("an entire expected generation never appears", samples(roots=("r1",)),
             expectations("10m", CertificateMetricsMissing=[("next-3", "root"), ("next-3", "signer")]))
        stale = " ".join(["7776000"] * 6 + ["stale"] + ["_"] * 10 + ["7776000"] * 164)
        case("a stale timestamp raises an alert and clears when restored", samples({
            ("r1", "signer", "expiry"): stale,
        }), expectations("15m"), expectations("16m", CertificateMetricsMissing=[("r1", "signer")]),
             expectations("17m"))
        case("zero renewal is uninitialized, not an ancient overdue renewal", samples({
            ("r1", "signer", "renewal"): "0+0x180",
        }), expectations("2h", CertificateMetricsMissing=[("r1", "signer")]))
        for source, name in (("0+0x180", "down"), (None, "absent")):
            case(f"{name} source reports unknown health without missing-series flood",
                 samples(roots=(), source=source), expectations("9m"),
                 expectations("10m", CertificateSourceUnavailable=[None]))
        case("source up with no certificate series", samples(roots=()),
             expectations("10m", CertificateMetricsMissing=[(i, r) for i in ROOT_IDS for r in ("root", "signer")]))
        not_ready = samples({("r1", "signer", "ready"): "0+0x180", ("next-3", "root", "ready"): "0+0x180"})
        for root_id, role, condition in (("r1", "signer", "False"), ("next-3", "root", "Unknown")):
            other = certificate_series(root_id, role, "ready", "1+0x180")
            other["series"] = other["series"].replace('condition="True"', f'condition="{condition}"')
            not_ready.append(other)
        case("False and Unknown readiness both fail", not_ready, expectations("9m"),
             expectations("10m", CertificateNotReady=[("r1", "signer"), ("next-3", "root")]))
        case("expiry thresholds and hold", samples({
            ("r1", "root", "expiry"): f"{365 * 86400 + 1800}+0x180",
            ("r1", "root", "renewal"): "1800+0x180",
            ("r1", "signer", "expiry"): f"{14 * 86400 + 1800}+0x180",
        }), expectations("30m"), expectations("40m"),
             expectations("41m", CertificateExpiringSoon=[("r1", "root"), ("r1", "signer")]))
        case("root rollover warns before the one-year renewal window", samples({
            ("next-3", "root", "renewal"): f"{89 * 86400}+0x180",
            ("next-3", "root", "expiry"): f"{454 * 86400}+0x180",
        }), expectations("59m"), expectations("1h", RootRolloverDue=[("next-3", "root")]))
        wrong_issuer = samples()
        for item in wrong_issuer:
            if 'name="agentgateway-inspection-signer-next-3-certificate"' in item["series"]:
                item["series"] = item["series"].replace('issuer_name="agentgateway-inspection-root-next-3-issuer"',
                                                        'issuer_name="unrelated-issuer"')
        case("a same-name certificate from another issuer cannot satisfy expected telemetry", wrong_issuer,
             expectations("10m", CertificateMetricsMissing=[("next-3", "signer")]))
        self.check_rule_tests(rules, tests)

    @unittest.skipUnless(PROMTOOL, "promtool unavailable; supply an existing binary via PROMTOOL")
    def test_disabled_inspection_or_monitoring_evaluates_without_alerts(self):
        for inspection, monitoring in ((False, True), (True, False)):
            with self.subTest(inspection=inspection, monitoring=monitoring):
                rendered = documents(render(CHART, {"agentgateway": {"inspection": {
                    "enabled": inspection, "monitoring": {"enabled": monitoring},
                    "rootIds": list(ROOT_IDS),
                }}}, value_files=()))
                rules = {"groups": [group for doc in rendered if doc["kind"] == "PrometheusRule"
                                    for group in doc["spec"]["groups"]]}
                broken = samples({
                    (root_id, role, metric): "0+0x180" if metric == "ready" else "600+0x180"
                    for root_id in ROOT_IDS for role in ("root", "signer") for metric in METRICS
                })
                self.check_rule_tests(rules, [{
                    "name": f"inspection={inspection}, monitoring={monitoring}, {name}",
                    "interval": "1m", "input_series": data,
                    "promql_expr_test": expectations("2h"),
                } for name, data in (("no source", []), ("unhealthy certificates", broken))])


if __name__ == "__main__":
    unittest.main()
