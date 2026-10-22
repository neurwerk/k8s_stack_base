"""Optional workload isolation and credential-reference safety."""

import unittest

import yaml

from helm import ROOT, documents, render, resource


class GlitchTipTests(unittest.TestCase):
    def test_unselected_chart_has_no_workloads_storage_or_authority(self):
        self.assertEqual(render("glitchtip", {"glitchtip": {"enabled": False}}).stdout.strip(), "")

    def adapter_values(self):
        # Use the verified published image; offline rendering never runs it.
        image = yaml.safe_load((ROOT / "charts/glitchtip/values.yaml").read_text())["glitchtip"]["adapter"]["image"]
        return {"glitchtip": {"adapter": {
            "enabled": True,
            "image": image,
            "initialStartAt": "2026-10-22T00:00:00Z",
            "scopes": [{
                "id": "example-api", "application": "example", "service": "api",
                "namespaces": ["example"], "containers": ["api"],
                "failure_types": ["exception"],
            }],
        }}}

    def test_adapter_disabled_has_no_state_or_credentials_consumer(self):
        result = render("glitchtip")
        self.assertNotIn("glitchtip-adapter-state", result.stdout)
        self.assertNotIn("ADAPTER_OPENSEARCH_PASSWORD", result.stdout)
        deployment = resource(result, "Deployment", "glitchtip")
        pod = deployment["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(deployment["spec"]["strategy"], {"type": "Recreate"})
        env = pod["containers"][0]["env"]
        for variable in env:
            self.assertNotIn("value", variable)
            self.assertNotIn("optional", variable["valueFrom"]["secretKeyRef"])

    def test_adapter_cannot_activate_without_reviewed_inputs(self):
        result = render("glitchtip", {"glitchtip": {"adapter": {"enabled": True}}}, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_application_egress_does_not_allow_adapter_database_access(self):
        policy = resource(render("glitchtip"), "NetworkPolicy", "glitchtip-application")
        self.assertEqual(policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"], "application")
        rules = policy["spec"]["egress"]
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[1]["ports"], [{"protocol": "TCP", "port": 9712}])
        peer = rules[1]["to"][0]
        self.assertEqual(peer["namespaceSelector"]["matchLabels"], {"kubernetes.io/metadata.name": "infra-postgres-operations"})
        self.assertEqual(peer["podSelector"]["matchLabels"], {
            "app.kubernetes.io/name": "postgres-operations",
            "app.kubernetes.io/instance": "postgres-operations",
        })
        for doc in documents(render("glitchtip")):
            self.assertNotIn(doc["kind"], ("Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding"))

    def test_adapter_has_only_ingestion_credentials_and_restricted_egress(self):
        result = render("glitchtip", self.adapter_values())
        deployment = resource(result, "Deployment", "glitchtip-adapter")
        pod = deployment["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(deployment["spec"]["replicas"], 1)
        self.assertEqual(deployment["spec"]["strategy"], {"type": "Recreate"})
        container = pod["containers"][0]
        self.assertEqual(container["command"], ["opensearch-glitchtip-adapter"])
        secret_keys = {
            item["valueFrom"]["secretKeyRef"]["key"]
            for item in container["env"] if "valueFrom" in item
        }
        self.assertEqual(secret_keys, {
            "ADAPTER_OPENSEARCH_USERNAME", "ADAPTER_OPENSEARCH_PASSWORD", "ADAPTER_GLITCHTIP_DSN",
        })
        policy = resource(result, "NetworkPolicy", "glitchtip-adapter")["spec"]
        self.assertEqual(policy["ingress"], [])
        self.assertEqual(len(policy["egress"]), 3)
        self.assertEqual(
            [port["port"] for rule in policy["egress"] for port in rule["ports"]],
            [53, 53, 9200, 8000],
        )
        for rule in policy["egress"]:
            for peer in rule["to"]:
                self.assertNotIn("ipBlock", peer)
                self.assertIn("podSelector", peer)

    def test_scope_query_injection_is_rejected_before_resources(self):
        values = self.adapter_values()
        values["glitchtip"]["adapter"]["scopes"][0]["query"] = "source=private-*"
        result = render("glitchtip", values, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_public_root_rewrites_only_to_one_status_view(self):
        result = render("glitchtip", {"glitchtip": {"gateway": {"status": {
            "enabled": True, "pagePath": "/status-pages/example/public/",
        }}}})
        route = resource(result, "HTTPRoute", "glitchtip-status")
        rules = route["spec"]["rules"]
        paths = rules[0]["matches"]
        self.assertEqual([(item["path"]["value"], item["path"]["type"]) for item in paths], [
            ("/", "Exact"), ("/status-pages/example/public/", "Exact"),
        ])
        self.assertEqual(rules[0]["filters"], [{
            "type": "URLRewrite", "urlRewrite": {
                "path": {"type": "ReplaceFullPath", "replaceFullPath": "/status-pages/example/public/"},
            },
        }])
        gateway = resource(result, "Gateway", "glitchtip")
        self.assertEqual(len(gateway["spec"]["listeners"]), 1)
        self.assertEqual(gateway["spec"]["listeners"][0]["hostname"], "status.example.com")
        self.assertEqual(gateway["metadata"]["annotations"]["cert-manager.io/cluster-issuer"], "letsencrypt-production-cluster-issuer")
        invalid = render("glitchtip", {"glitchtip": {"gateway": {"status": {
            "enabled": True, "pagePath": "/admin/",
        }}}}, check=False)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(invalid.stdout, "")

    def test_private_ui_is_tailnet_tls_service_not_a_public_route(self):
        invalid = render("glitchtip", {"glitchtip": {"gateway": {"ui": {
            "enabled": True,
        }}}}, check=False)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(invalid.stdout, "")
        result = render("glitchtip", {"glitchtip": {"gateway": {"ui": {
            "enabled": True, "tailscale": {"hostname": "glitchtip-example", "tags": ["tag:glitchtip-ui"]},
        }}}})
        self.assertTrue(all(doc["kind"] not in ("Gateway", "HTTPRoute", "Ingress", "Middleware") for doc in documents(result)))
        service = resource(result, "Service", "glitchtip-ui")
        self.assertEqual(service["spec"]["type"], "ClusterIP")
        self.assertEqual(service["spec"]["ports"], [{"name": "https", "port": 443, "targetPort": "ui-https"}])
        self.assertEqual(service["metadata"]["annotations"], {
            "tailscale.com/expose": "true", "tailscale.com/hostname": "glitchtip-example",
            "tailscale.com/tags": "tag:glitchtip-ui",
        })
        self.assertEqual(resource(result, "Certificate", "glitchtip-ui")["spec"]["dnsNames"], ["glitchtip.example.com"])
        containers = resource(result, "Deployment", "glitchtip")["spec"]["template"]["spec"]["containers"]
        self.assertEqual(containers[0]["image"], containers[1]["image"])
        ui = next(container for container in containers if container["name"] == "ui")
        self.assertEqual(ui["command"], ["./bin/run-granian.sh"])
        self.assertEqual(ui["args"], ["--ssl-certificate", "/etc/glitchtip-ui-tls/tls.crt", "--ssl-keyfile", "/etc/glitchtip-ui-tls/tls.key"])
        policy = resource(result, "NetworkPolicy", "glitchtip-application")["spec"]
        private = next(rule for rule in policy["ingress"] if rule["ports"][0]["port"] == 8443)
        self.assertEqual(private["from"], [{
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "tailscale"}},
            "podSelector": {"matchLabels": {
                "tailscale.com/managed": "true", "tailscale.com/parent-resource": "glitchtip-ui",
                "tailscale.com/parent-resource-ns": "catalog-test", "tailscale.com/parent-resource-type": "svc",
            }},
        }])
        self.assertNotIn("100.64", result.stdout)
        combined = render("glitchtip", {"glitchtip": {"gateway": {
            "ui": {"enabled": True, "tailscale": {"hostname": "glitchtip-example", "tags": ["tag:glitchtip-ui"]}},
            "status": {"enabled": True, "pagePath": "/status-pages/example/public/"},
        }}})
        self.assertEqual([listener["name"] for listener in resource(combined, "Gateway", "glitchtip")["spec"]["listeners"]], ["status"])
        self.assertEqual([doc["metadata"]["name"] for doc in documents(combined) if doc["kind"] == "HTTPRoute"], ["glitchtip-status"])

    def test_reader_role_is_opt_in_and_cannot_write(self):
        disabled = render("opensearch/app")
        self.assertNotIn("glitchtip_adapter_read", disabled.stdout)
        disabled = render("opensearch/app", {"glitchtip": {"enabled": False, "adapter": {"enabled": True}}})
        self.assertNotIn("glitchtip_adapter_read", disabled.stdout)
        enabled = render("opensearch/app", {"glitchtip": {"adapter": {"enabled": True}}})
        config = next(doc for doc in documents(enabled) if doc["kind"] == "ConfigMap" and "roles.yml" in doc.get("data", {}))
        role = yaml.safe_load(config["data"]["roles.yml"])["glitchtip_adapter_read"]
        self.assertEqual(role["cluster_permissions"], ["cluster:admin/opensearch/ppl"])
        self.assertEqual(role["index_permissions"], [{
            "index_patterns": ["fluent-bit-*"],
            "allowed_actions": ["read", "indices:admin/mappings/get", "indices:monitor/settings/get"],
        }])

    def test_native_provisioner_isolated_from_adapter_and_secret_controllers(self):
        image = self.adapter_values()["glitchtip"]["adapter"]["image"]
        result = render("glitchtip", {"glitchtip": {"provisioning": {
            "enabled": True, "image": image,
            "config": {
                "organization": {"slug": "example", "name": "Example"},
                "team": {"slug": "operations"},
                "project": {"slug": "operations", "name": "Operations"},
                "alert": {"name": "First occurrence", "alertRecipients": [{"recipientType": "email"}]},
            },
        }}})
        job = resource(result, "Job", "glitchtip-provisioning")
        self.assertEqual(job["metadata"]["annotations"]["helm.sh/hook"], "post-install,post-upgrade")
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        init = pod["initContainers"][0]
        main = pod["containers"][0]
        self.assertEqual(init["command"], ["provision-glitchtip", "export", "--output", "/payload/glitchtip.py"])
        self.assertNotIn("envFrom", init)
        self.assertNotIn("env", init)
        self.assertEqual(main["command"], ["python", "/payload/glitchtip.py", "provision"])
        self.assertEqual(main["image"], resource(result, "Deployment", "glitchtip")["spec"]["template"]["spec"]["containers"][0]["image"])
        self.assertEqual(main["workingDir"], "/code")
        self.assertTrue(next(item for item in main["volumeMounts"] if item["name"] == "payload")["readOnly"])
        self.assertTrue(all("persistentVolumeClaim" not in volume for volume in pod["volumes"]))
        self.assertEqual([item["secretRef"]["name"] for item in main["envFrom"] if "secretRef" in item], ["glitchtip-owner"])
        selector = resource(result, "Service", "glitchtip")["spec"]["selector"]
        labels = job["spec"]["template"]["metadata"]["labels"]
        self.assertTrue(any(labels.get(key) != value for key, value in selector.items()))
        policy = resource(result, "NetworkPolicy", "glitchtip-provisioning")["spec"]
        self.assertEqual([port["port"] for rule in policy["egress"] for port in rule["ports"]], [53, 53, 9712])
        self.assertNotIn("ADAPTER_GLITCHTIP_DSN", result.stdout)
        self.assertNotIn("apiToken", result.stdout)

    def test_runtime_delivery_cannot_read_shared_database_record(self):
        import subprocess

        result = subprocess.run(
            ["kustomize", "build", str(ROOT / "releases/glitchtip/secret-sync")],
            check=True, capture_output=True, text=True,
        )
        secrets = [doc for doc in yaml.safe_load_all(result.stdout) if doc and doc["kind"] == "ExternalSecret" and doc["metadata"]["namespace"] == "glitchtip"]
        self.assertTrue(secrets)
        for secret in secrets:
            for field in secret["spec"]["data"]:
                self.assertTrue(field["remoteRef"]["key"].startswith("glitchtip/"))
        runtime = next(secret for secret in secrets if secret["metadata"]["name"] == "glitchtip-runtime")
        database = next(field for field in runtime["spec"]["data"] if field["secretKey"] == "postgresqlPassword")
        self.assertEqual(database["remoteRef"], {"key": "glitchtip/internal", "property": "databasePassword"})

    def test_database_password_round_trip_uses_uri_not_query_decoding(self):
        import json
        import subprocess
        import tempfile
        from pathlib import Path
        from urllib.parse import unquote, urlsplit

        runtime = next(
            doc for doc in yaml.safe_load_all(
                (ROOT / "releases/glitchtip/secret-sync/runtime.yaml").read_text()
            ) if doc and doc["kind"] == "ExternalSecret"
        )
        template = runtime["spec"]["target"]["template"]["data"]["DATABASE_URL"]
        # Helm tpl runs the actual Go/Sprig template, not a Python encoding copy.
        with tempfile.TemporaryDirectory(prefix="glitchtip-password-") as directory:
            chart = Path(directory)
            (chart / "templates").mkdir()
            (chart / "Chart.yaml").write_text("apiVersion: v2\nname: password-regression\nversion: 0.1.0\n")
            (chart / "templates" / "url.yaml").write_text(
                "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: password-regression\n"
                "data:\n  url: {{ tpl .Values.source .Values.synthetic | quote }}\n"
            )
            for password in ("spaces inside", "literal+plus", "a b+c/@:%?#&=é"):
                with self.subTest(password=password):
                    result = subprocess.run(
                        ["helm", "template", "password-regression", str(chart), "--values", "-"],
                        input=json.dumps({"source": template, "synthetic": {"postgresqlPassword": password}}),
                        text=True, capture_output=True, check=True,
                    )
                    url = yaml.safe_load(result.stdout)["data"]["url"]
                    parsed = urlsplit(url)
                    # django-environ 0.13.0 uses unquote, not unquote_plus.
                    self.assertEqual(unquote(parsed.password), password)
                    self.assertEqual(parsed.hostname, "postgres-operations.infra-postgres-operations.svc.cluster.local")
                    self.assertEqual(parsed.path, "/glitchtip")
                    if " " in password:
                        self.assertIn("%20", parsed.password)
                    if "+" in password:
                        self.assertIn("%2B", parsed.password)

    def test_unselected_composition_is_unchanged_and_database_gates_app(self):
        import subprocess

        for package in ("namespaces", "infrastructure", "applications"):
            result = subprocess.run(
                ["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone", str(ROOT / "releases" / package)],
                text=True, capture_output=True, check=True,
            )
            self.assertNotIn("glitchtip", result.stdout)
        app = yaml.safe_load((ROOT / "releases/glitchtip/app/app.yaml").read_text())
        self.assertIn({"name": "glitchtip-postgres", "namespace": "infra-postgres-operations"}, app["spec"]["dependsOn"])
