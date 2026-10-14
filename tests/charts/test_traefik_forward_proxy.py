"""Protect the default-off, exact-SNI TCP passthrough boundary."""

import copy
import unittest

import yaml

from helm import documents, render, resource


VALUES = {
    "aiGateway": {
        "enabled": True,
        "proxy": {"enabled": True, "hostname": "proxy.platform.test"},
    },
}


def edge(values):
    return render("traefik", values, namespace="kube-system", value_files=(), check=False)


def vendor_values(result):
    return yaml.safe_load(resource(result, "HelmChartConfig")["spec"]["valuesContent"])


class TraefikForwardProxyTests(unittest.TestCase):
    def test_both_boolean_gates_are_required(self):
        baseline = edge({})
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        for gateway_enabled, proxy_enabled in ((False, False), (True, False), (False, True)):
            with self.subTest(gateway=gateway_enabled, proxy=proxy_enabled):
                result = edge({"aiGateway": {
                    "enabled": gateway_enabled, "proxy": {"enabled": proxy_enabled},
                }})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, baseline.stdout)
                self.assertNotIn("forward-proxy", vendor_values(result)["ports"])
                self.assertFalse(any(doc["kind"] == "IngressRouteTCP" for doc in documents(result)))

    def test_only_exact_sni_reaches_fixed_backend_without_tls_termination(self):
        for port in (8443, 9443):
            with self.subTest(port=port):
                values = copy.deepcopy(VALUES)
                if port != 8443:
                    values["aiGateway"]["proxy"]["port"] = port
                result = edge(values)
                self.assertEqual(result.returncode, 0, result.stderr)
                vendor = vendor_values(result)
                entry = vendor["ports"]["forward-proxy"]
                for field in ("port", "exposedPort", "hostPort"):
                    self.assertEqual(entry[field], port)
                self.assertEqual(entry["protocol"], "TCP")
                self.assertTrue(entry["expose"]["default"])
                for field in ("http", "tls", "http3", "asDefault"):
                    self.assertNotIn(field, entry)
                self.assertEqual(
                    {name for name, config in vendor["ports"].items() if config.get("asDefault")},
                    {"web", "websecure"},
                )
                route = resource(result, "IngressRouteTCP")
                self.assertEqual(route["metadata"]["namespace"], "infra-agentgateway")
                self.assertEqual(route["spec"]["entryPoints"], ["forward-proxy"])
                rule, = route["spec"]["routes"]
                self.assertEqual(rule["match"], "HostSNI(`proxy.platform.test`)")
                self.assertEqual(rule["services"], [{"name": "infra-agentgateway-forward-proxy", "port": port}])
                self.assertEqual(route["spec"]["tls"], {"passthrough": True})

    def test_rejects_ambiguous_or_unsafe_exposure_inputs(self):
        cases = [
            ("aiGateway.enabled", value) for value in ("true", "false", 1, None)
        ] + [
            ("aiGateway.proxy.enabled", value) for value in ("true", "false", 1, None)
        ] + [
            ("aiGateway.proxy.hostname", value) for value in (
                "", "*", "*.platform.test", "https://proxy.platform.test", "proxy.platform.test:8443",
                "proxy.platform.test.", "Proxy.platform.test", "127.0.0.1", "localhost",
                "proxy.platform.test`) || HostSNI(`*", "proxy.platform.test\n", "a" * 64 + ".test",
                ".".join(["a" * 63] * 4), None,
            )
        ] + [
            ("aiGateway.proxy.port", value) for value in (0, -1, 65536, 8443.5, "8443", True, None)
        ]
        for path, value in cases:
            with self.subTest(path=path, value=value):
                values = copy.deepcopy(VALUES)
                parent = values
                parts = path.split(".")
                for part in parts[:-1]:
                    parent = parent[part]
                parent[parts[-1]] = value
                result = edge(values)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("aiGateway", result.stderr)

    def test_rejects_port_collisions_and_missing_crd_provider(self):
        cases = [({"port": port}, {}) for port in (80, 443, 8000, 8080, 9100)]
        cases += [
            ({"port": 9443}, {"ports": {name: {field: 9443}}})
            for name in ("web", "websecure") for field in ("port", "exposedPort")
        ]
        for proxy, traefik in cases:
            with self.subTest(proxy=proxy, traefik=traefik):
                values = copy.deepcopy(VALUES)
                values["aiGateway"]["proxy"].update(proxy)
                values["traefik"] = traefik
                result = edge(values)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("aiGateway.proxy.port collides", result.stderr)
        values = copy.deepcopy(VALUES)
        values["traefik"] = {"providers": {"kubernetesCRD": {"enabled": False}}}
        result = edge(values)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("requires traefik.providers.kubernetesCRD.enabled: true", result.stderr)


if __name__ == "__main__":
    unittest.main()
