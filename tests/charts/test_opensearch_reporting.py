"""Behavioral tests for convergent OpenSearch reporting provisioning."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "charts/opensearch/dashboards/files/provision_reporting.py"
VALUES = ROOT / "charts/opensearch/dashboards/values.yaml"
SECURITY_CONFIG = (
    ROOT / "charts/opensearch/app/templates/configmap-security-config.yaml"
)
CRONJOB = ROOT / "charts/opensearch/dashboards/templates/reporting-cronjob.yaml"


def load_provisioner():
    sys.modules.setdefault(
        "requests",
        SimpleNamespace(
            Session=object,
            RequestException=RuntimeError,
            exceptions=SimpleNamespace(RequestException=RuntimeError),
        ),
    )
    spec = importlib.util.spec_from_file_location("provision_reporting", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.DATA_SOURCE_URI = "http://prometheus:9090"
    module.READER_ROLE = "opensearch_reporting_reader"
    return module


class Response:
    def __init__(self, body, status_code=200, method="GET", url="http://dashboards"):
        self._body = body
        self.status_code = status_code
        self.request = SimpleNamespace(method=method)
        self.url = url

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Session:
    def __init__(self, backend, saved_object_responses):
        self.backend = backend
        self.saved_object_responses = iter(saved_object_responses)
        self.calls = []

    def get(self, url, timeout):
        self.calls.append(("GET", url, {}))
        return Response(self.backend, url=url)

    def request(self, method, url, timeout, **kwargs):
        self.calls.append((method, url, kwargs))
        if "_find" in url:
            return Response(next(self.saved_object_responses), method=method, url=url)
        return Response({"success": True}, method=method, url=url)


class OpenSearchReportingProvisioningTests(unittest.TestCase):
    def test_reader_has_saved_object_access_and_report_preflight(self) -> None:
        security_config = SECURITY_CONFIG.read_text(encoding="utf-8")
        cronjob = CRONJOB.read_text(encoding="utf-8")

        reader_role = security_config.split(
            "    {{ .Values.monitorOpensearchWrapper.reportingReaderRole }}:", 1
        )[1].split(
            "  roles_mapping.yml:", 1
        )[0]
        self.assertIn('- ".kibana"', reader_role)
        self.assertIn('- ".kibana_*"', reader_role)
        self.assertIn('- "read"', reader_role)
        self.assertIn("verify-dashboard-access", cronjob)
        self.assertIn("response.raise_for_status()", cronjob)

    def test_dashboards_reporting_features_are_enabled(self) -> None:
        values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
        config = values["opensearch-dashboards"]["config"]["opensearch_dashboards.yml"]

        self.assertIn("data_source.enabled: true", config)
        self.assertIn("explore.enabled: true", config)

    def test_missing_saved_object_recreates_backend_and_saved_object(self) -> None:
        provisioner = load_provisioner()
        backend = {
            "name": provisioner.DATA_SOURCE_NAME,
            "connector": "prometheus",
            "allowedRoles": [provisioner.READER_ROLE],
            "properties": {"prometheus.uri": provisioner.DATA_SOURCE_URI},
        }
        saved_object = {
            "id": "connection-object-id",
            "attributes": {
                "connectionId": provisioner.DATA_SOURCE_NAME,
                "type": "Prometheus",
            },
        }
        session = Session(
            backend,
            [
                {"saved_objects": []},
                {"saved_objects": []},
                {"saved_objects": [saved_object]},
            ],
        )

        self.assertEqual(
            provisioner.converge_data_source(session), "connection-object-id"
        )
        self.assertTrue(
            any(
                method == "GET"
                and url.endswith(
                    f"/{provisioner.DATA_SOURCE_NAME}/dataSourceMDSId="
                )
                for method, url, _ in session.calls
            )
        )
        self.assertTrue(
            any(
                method == "DELETE"
                and url.endswith(
                    f"/{provisioner.DATA_SOURCE_NAME}/dataSourceMDSId="
                )
                for method, url, _ in session.calls
            )
        )
        self.assertTrue(
            any(
                method == "POST" and url.endswith("dataconnections")
                for method, url, _ in session.calls
            )
        )

    def test_backend_connector_comparison_is_case_insensitive(self) -> None:
        provisioner = load_provisioner()
        backend = {
            "name": provisioner.DATA_SOURCE_NAME,
            "connector": "PROMETHEUS",
            "allowedRoles": [provisioner.READER_ROLE],
            "properties": {"prometheus.uri": provisioner.DATA_SOURCE_URI},
        }

        self.assertTrue(provisioner.backend_matches(backend))

    def test_successful_http_with_import_errors_is_rejected(self) -> None:
        provisioner = load_provisioner()
        response = Response({"success": True, "errors": [{"type": "dashboard"}]})

        with self.assertRaisesRegex(RuntimeError, "failed"):
            provisioner.checked_json(response)


if __name__ == "__main__":
    unittest.main()
