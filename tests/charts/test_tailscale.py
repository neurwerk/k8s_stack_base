"""Optional Tailscale composition and narrowly scoped credential delivery."""

import subprocess
import unittest

import yaml

from helm import ROOT, documents, env_value, render, resource


def package(path):
    output = subprocess.check_output(
        ["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone", str(ROOT / path)],
        text=True,
    )
    return [doc for doc in yaml.safe_load_all(output) if doc]


class TailscaleTests(unittest.TestCase):
    def test_defaults_and_explicit_disabled_configuration_render_empty(self):
        for values in [{}, {"tailscale": {
            "enabled": False,
            "loginServer": "https://headscale.example.test",
            "operatorConfig": {"hostname": "synthetic-operator"},
            "api": {"enabled": True, "hostname": "kube-api-test", "tags": ["tag:kube-api-test"]},
        }}]:
            for chart in ["tailscale/operator", "tailscale/access"]:
                with self.subTest(values=values, chart=chart):
                    self.assertEqual(render(chart, values, value_files=()).stdout.strip(), "")
        self.assertEqual(render("tailscale/access", {"tailscale": {"enabled": True}}, value_files=()).stdout.strip(), "")

    def test_default_stages_do_not_own_tailscale(self):
        namespace = package("releases/namespaces/tailscale")
        self.assertEqual(len(namespace), 1)
        self.assertEqual(namespace[0]["kind"], "Namespace")
        self.assertNotIn("secrets.neurwerk.com/openbao-trust", namespace[0]["metadata"].get("labels", {}))
        for stage in ["namespaces", "infrastructure", "applications"]:
            with self.subTest(stage=stage):
                for doc in package(f"releases/{stage}"):
                    metadata = doc.get("metadata", {})
                    self.assertNotEqual(metadata.get("namespace"), "tailscale")
                    self.assertNotIn("tailscale", metadata.get("name", ""))

    def test_supported_control_planes_use_existing_secret_without_api_proxy(self):
        for login_server in ["", "https://headscale.example.test"]:
            with self.subTest(login_server=login_server):
                result = render("tailscale/operator", {"tailscale": {
                    "enabled": True, "loginServer": login_server,
                }}, namespace="tailscale", value_files=())
                deployment = resource(result, "Deployment", "operator")
                pod = deployment["spec"]["template"]["spec"]
                self.assertIn({"name": "oauth", "secret": {"secretName": "operator-oauth"}}, pod["volumes"])
                self.assertEqual(env_value(result, "CLIENT_ID_FILE"), "/oauth/client_id")
                self.assertEqual(env_value(result, "CLIENT_SECRET_FILE"), "/oauth/client_secret")
                self.assertEqual(env_value(result, "OPERATOR_LOGIN_SERVER"), login_server or None)
                self.assertEqual(env_value(result, "APISERVER_PROXY"), "false")
                self.assertFalse(any(doc["kind"] in [
                    "Secret", "Ingress", "Service", "Connector", "ProxyGroup", "ProxyClass",
                ] for doc in documents(result)))
                for doc in documents(result):
                    if doc["kind"] == "ClusterRole":
                        self.assertFalse(any("impersonate" in rule["verbs"] for rule in doc["rules"]))
                extra_env = pod["containers"][0]["env"]
                self.assertFalse(any(item["name"] in [
                    "TS_ROUTES", "TS_EXTRA_ARGS", "TS_SSH", "TS_SERVE_CONFIG",
                ] for item in extra_env))

    def test_api_transport_owns_only_the_selected_external_name_service(self):
        for cluster_domain in ["cluster.local", "cluster.example.test"]:
            with self.subTest(cluster_domain=cluster_domain):
                values = {"tailscale": {"enabled": True, "api": {
                    "enabled": True, "hostname": "kube-api-test", "tags": ["tag:kube-api-test"],
                    "clusterDomain": cluster_domain,
                }}}
                result = render("tailscale/access", values, namespace="tailscale", value_files=())
                self.assertEqual(documents(result), [{
                    "apiVersion": "v1", "kind": "Service",
                    "metadata": {"name": "kube-api-tailnet", "namespace": "tailscale", "annotations": {
                        "tailscale.com/expose": "true",
                        "tailscale.com/hostname": "kube-api-test",
                        "tailscale.com/tags": "tag:kube-api-test",
                    }},
                    "spec": {
                        "type": "ExternalName", "externalName": f"kubernetes.default.svc.{cluster_domain}",
                        "ports": [{"name": "https", "protocol": "TCP", "port": 443}],
                    },
                }])
                # The existing Kubernetes Service is untouched. Service ports are
                # metadata, NOT a filter on upstream address-based forwarding.
                operator = render("tailscale/operator", values, namespace="tailscale", value_files=())
                self.assertEqual(env_value(operator, "APISERVER_PROXY"), "false")

    def test_rejects_unsupported_or_unsafe_inputs(self):
        for invalid in [
            {"enabled": "true"},
            {"oauthSecret": ""},
            {"loginServer": "http://headscale.example.test"},
            {"oauth": {"clientSecret": "not-an-approved-input"}},
            {"apiServerProxyConfig": {"mode": "noauth"}},
            {"apiServerProxyConfig": {"mode": "true", "allowImpersonation": "true"}},
            {"operatorConfig": {"extraEnv": [{"name": "TS_EXTRA_ARGS", "value": "--ssh"}]}},
            {"subnetRoutes": ["192.0.2.0/24"]},
            {"ssh": True},
            {"funnel": True},
        ]:
            with self.subTest(invalid=invalid):
                result = render("tailscale/operator", {"tailscale": {"enabled": True, **invalid}},
                                value_files=(), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("schema", result.stderr)
        for invalid in [
            {"hostname": ""}, {"tags": []}, {"clusterDomain": ""},
            {"enabled": "true"}, {"mode": "noauth"},
            {"externalName": "unapproved.example.test"},
            {"ports": [22]}, {"ssh": True}, {"funnel": True},
            {"subnetRoutes": ["192.0.2.0/24"]},
        ]:
            with self.subTest(api=invalid):
                result = render("tailscale/access", {"tailscale": {"enabled": True, "api": {
                    "enabled": True, "hostname": "kube-api-test", "tags": ["tag:kube-api-test"], **invalid,
                }}}, value_files=(), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("schema", result.stderr)

    def test_secret_delivery_requests_only_approved_fields(self):
        docs = package("releases/tailscale/secret-sync")
        self.assertCountEqual(
            [doc["kind"] for doc in docs], ["ServiceAccount", "SecretStore", "ExternalSecret"]
        )
        account = next(doc for doc in docs if doc["kind"] == "ServiceAccount")
        self.assertEqual(account["metadata"]["name"], "tailscale-external-secrets")
        self.assertFalse(account["automountServiceAccountToken"])
        store = next(doc for doc in docs if doc["kind"] == "SecretStore")
        vault = store["spec"]["provider"]["vault"]
        self.assertEqual((vault["path"], vault["version"]), ("secret", "v2"))
        self.assertEqual(vault["caProvider"], {
            "type": "ConfigMap", "name": "infra-openbao-ca-bundle", "key": "ca.crt",
        })
        self.assertEqual(vault["auth"]["kubernetes"], {
            "mountPath": "kubernetes",
            "role": "tailscale",
            "serviceAccountRef": {"name": "tailscale-external-secrets", "audiences": ["openbao"]},
        })
        external = next(doc for doc in docs if doc["kind"] == "ExternalSecret")
        self.assertEqual(external["metadata"]["name"], "operator-oauth")
        self.assertEqual(external["spec"]["secretStoreRef"], {
            "name": "tailscale-openbao-secret-store", "kind": "SecretStore",
        })
        self.assertEqual(external["spec"]["target"], {
            "name": "operator-oauth", "creationPolicy": "Owner", "deletionPolicy": "Retain",
        })
        self.assertEqual(external["spec"]["data"], [
            {"secretKey": field, "remoteRef": {"key": "tailscale/external", "property": field}}
            for field in ["client_id", "client_secret"]
        ])
        self.assertNotIn("dataFrom", external["spec"])
