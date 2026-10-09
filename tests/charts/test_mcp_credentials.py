"""Credential ownership is chart-defined and never supplied by browser input."""

import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from helm import ROOT, documents, render, resource


def managed_values():
    return {"mcp": {"enabled": True, "studioSetup": {"enabled": True}, "catalog": {"presets": {
        "context7": {"enabled": True}, "brave": {"enabled": True},
        "github": {"enabled": True, "registration": {"oauth": {
            "client_id": "example-app", "redirect_uri": "https://studio.example.com/oauth/callback"}}},
    }}}}


def studio_values():
    return {"mcp": {"studioSetup": {"enabled": True}}, "frontendStudio": {"api": {
        "mcpSetup": {"kubernetesApiEgress": [{"cidr": "192.0.2.1/32", "port": 443},
                                             {"cidr": "198.51.100.1/32", "port": 6443}]},
        "contextforge": {"enabled": True, "accountOnboardingEnabled": True, "connectionsEnabled": True,
            "studioOrigin": "https://studio.example.com", "serviceAccountEmail": "studio@example.com",
            "setupConfigMapName": "contextforge-setup", "catalogConfigMapName": "contextforge-setup"}}}}


def native_values():
    return {"mcp": {"studioSetup": {"enabled": True}}, "contextforge": {
        "trustedProxy": {"enabled": True, "studioOrigin": "https://studio.example.com",
                         "defaultUserRole": "reader", "defaultTeamMemberRole": "member"},
        "setup": {"enabled": True, "serviceAccountEmail": "studio@example.com",
                  "kubernetesApiEgress": [{"cidr": "192.0.2.1/32", "port": 443}]}}}


class McpCredentialsTests(unittest.TestCase):
    def test_optional_shared_key_is_projected_without_a_secret_value(self):
        output = render("agentgateway", {"mcp": {"catalog": {"presets": {
            "context7": {"enabled": True},
        }}}})
        catalog = resource(output, "ConfigMap", "infra-agentgateway-mcp-catalog")
        entry, = json.loads(catalog["data"]["studio.json"])
        self.assertEqual(entry["credential"], {
            "owner": "shared", "required": False, "method": "gateway-header", "header": "CONTEXT7_API_KEY",
        })
        self.assertEqual(entry["upstream_url"], "https://mcp.context7.com/mcp")
        self.assertNotIn("apiKey", catalog["data"]["studio.json"])

    def test_preset_cannot_change_credential_owner(self):
        output = render("agentgateway", {"mcp": {"catalog": {"presets": {
            "context7": {"enabled": True, "credential": {
                "owner": "individual", "required": True, "method": "oauth",
            }},
        }}}}, check=False)
        self.assertNotEqual(output.returncode, 0)
        self.assertIn("credential policy cannot change", output.stderr)

    def test_initial_saved_and_removed_keys_bind_exact_runtime_versions(self):
        values = managed_values()
        for version, kv_version, configured in (("initial", 1, False), ("a" * 32, 2, True), ("b" * 32, 3, False)):
            with self.subTest(version=version):
                if version != "initial":
                    values["mcp"]["runtimeCredentials"] = {
                        identity: {"version": version, "kvVersion": kv_version, "configured": configured}
                        for identity in ("context7", "brave")}
                output = render("agentgateway", values, namespace="infra-agentgateway", release="infra-agentgateway")
                deployment = resource(output, "Deployment", "mcp-brave-deploy")
                self.assertEqual(deployment["spec"]["replicas"], 1 if configured else 0)
                self.assertEqual(deployment["spec"]["template"]["metadata"]["annotations"]["mcp.neurwerk.com/key-version"], version)
                self.assertEqual(deployment["spec"]["strategy"], {"type": "Recreate"})
                container, = deployment["spec"]["template"]["spec"]["containers"]
                env = {entry["name"]: entry for entry in container["env"]}
                self.assertNotIn("BRAVE_MCP_ENABLED_TOOLS", env)
                for identity in ("context7", "brave"):
                    suffix = hashlib.sha256(identity.encode()).hexdigest()[:12] + "-" + version
                    name = "mcp-key-" + suffix
                    secret = resource(output, "ExternalSecret", name)
                    self.assertEqual(secret["spec"]["data"], [{"secretKey": "apiKey", "remoteRef": {
                        "key": "mcp/shared/" + identity, "version": str(kv_version), "property": "apiKey"}}])
                    if identity == "brave":
                        self.assertEqual(env["BRAVE_API_KEY"]["valueFrom"]["secretKeyRef"], {"name": name, "key": "apiKey"})
                    else:
                        backend_name = "mcp-forward-" + suffix
                        backend = resource(output, "AgentgatewayBackend", backend_name)
                        if configured:
                            self.assertEqual(backend["spec"]["policies"]["auth"]["credentials"], [{
                                "secretRef": {"name": name, "key": "apiKey"},
                                "location": {"header": {"name": "CONTEXT7_API_KEY"}}}])
                        else:
                            self.assertNotIn("auth", backend["spec"]["policies"])
                        route = resource(output, "HTTPRoute", "mcp-forward-context7")
                        self.assertEqual(route["spec"]["rules"][0]["backendRefs"][0]["name"], backend_name)
                        self.assertEqual(backend["spec"]["static"], {"host": "mcp.context7.com", "port": 443})
                        # Studio reads the same exact backend as the active route,
                        # including after replacing or removing a shared key.
                        rules = resource(output, "Role", "studio-mcp-runtime")["rules"]
                        self.assertEqual([rule for rule in rules if rule["apiGroups"] == ["agentgateway.dev"]], [{
                            "apiGroups": ["agentgateway.dev"], "resources": ["agentgatewaybackends"],
                            "resourceNames": [backend_name], "verbs": ["get"]}])
                self.assertFalse(any(doc["kind"] == "Secret" and doc["metadata"]["name"].startswith("mcp-")
                                     for doc in documents(output)))
                self.assertNotIn("mcp-github-deploy", output.stdout)

    def test_eso_excludes_keys_from_values_and_runtime_rbac_is_read_only(self):
        output = render("agentgateway", managed_values(), namespace="infra-agentgateway", release="infra-agentgateway")
        store = resource(output, "SecretStore", "mcp-shared-delivery")
        auth = store["spec"]["provider"]["vault"]["auth"]["kubernetes"]
        self.assertEqual(auth, {"mountPath": "kubernetes", "role": "mcp-shared-delivery",
                                "serviceAccountRef": {"name": "mcp-shared-delivery", "audiences": ["openbao"]}})
        runtime = resource(output, "ExternalSecret", "mcp-runtime-values")["spec"]
        self.assertEqual({ref["remoteRef"]["key"] for ref in runtime["data"]}, {"mcp/shared/brave", "mcp/shared/context7"})
        template = runtime["target"]["template"]
        self.assertEqual(template["mergePolicy"], "Replace")
        self.assertEqual(set(template["data"]), {"values.yaml"})
        self.assertEqual(template["metadata"]["labels"], {"reconcile.fluxcd.io/watch": "Enabled"})
        # Execute the ESO Go-template expressions with Helm's matching Sprig
        # functions. Return only the parsed metadata, never the synthetic key.
        for key in ("", "synthetic-only"):
            with tempfile.TemporaryDirectory() as directory:
                chart = Path(directory)
                (chart / "Chart.yaml").write_text("apiVersion: v2\nname: metadata-test\nversion: 0.1.0\n")
                (chart / "templates").mkdir()
                (chart / "templates/values.yaml").write_text("{{- with .Values.records }}\n" + template["data"]["values.yaml"] + "\n{{- end }}\n")
                data = {ref["secretKey"]: json.dumps({"apiKey": key, "version": "a" * 32,
                        "kvVersion": 2, "operationId": "operation"}) for ref in runtime["data"]}
                result = subprocess.run(["helm", "template", "metadata-test", str(chart), "--values", "-"],
                    input=json.dumps({"records": data}), capture_output=True, text=True, check=True)
                metadata = yaml.safe_load(result.stdout)["mcp"]["runtimeCredentials"]
                self.assertEqual(metadata, {identity: {"version": "a" * 32, "kvVersion": 2, "configured": bool(key)}
                                           for identity in ("brave", "context7")})
        release = yaml.safe_load((ROOT / "releases/agentgateway/app.yaml").read_text())
        self.assertEqual(release["spec"]["valuesFrom"][-1], {"kind": "Secret", "name": "mcp-runtime-values",
                                                            "valuesKey": "values.yaml", "optional": True})
        deployment_rule = {"apiGroups": ["apps"], "resources": ["deployments"],
                           "resourceNames": ["mcp-brave-deploy"], "verbs": ["get"]}
        backend_rule = {"apiGroups": ["agentgateway.dev"], "resources": ["agentgatewaybackends"],
                        "resourceNames": ["mcp-forward-" + hashlib.sha256(b"context7").hexdigest()[:12] + "-initial"],
                        "verbs": ["get"]}
        for selected, expected in ((["brave", "context7", "github"], [deployment_rule, backend_rule]),
                                   (["context7"], [backend_rule]), (["brave"], [deployment_rule]), (["github"], [])):
            with self.subTest(providers=selected):
                values = managed_values()
                for identity, preset in values["mcp"]["catalog"]["presets"].items():
                    preset["enabled"] = identity in selected
                scoped = render("agentgateway", values, namespace="infra-agentgateway", release="infra-agentgateway")
                if not expected:
                    self.assertFalse(any(doc["kind"] in ("Role", "RoleBinding")
                                         and doc["metadata"]["name"] == "studio-mcp-runtime" for doc in documents(scoped)))
                    continue
                role = resource(scoped, "Role", "studio-mcp-runtime")
                # Empty resourceNames would grant namespace-wide reads. Omit an
                # unneeded rule entirely, and never add Secret/list/write access.
                self.assertEqual(role["rules"], expected)
                self.assertEqual(role["metadata"]["namespace"], "infra-agentgateway")
                binding = resource(scoped, "RoleBinding", role["metadata"]["name"])
                self.assertEqual(binding["metadata"]["namespace"], role["metadata"]["namespace"])
                self.assertEqual(binding["roleRef"], {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role",
                                                     "name": role["metadata"]["name"]})
                self.assertEqual(binding["subjects"], [{"kind": "ServiceAccount", "name": "studio-mcp", "namespace": "frontend-studio"}])
        registrations = json.loads(resource(output, "ConfigMap", "infra-agentgateway-mcp-catalog")["data"]["registrations.json"])
        self.assertTrue(all(entry["studio_managed"] for entry in registrations))
        context7 = next(entry for entry in registrations if entry["id"] == "context7")
        self.assertEqual(context7["upstream_url"], "http://contextforge-providers.infra-agentgateway.svc.cluster.local:8080/context7")

    def test_cross_chart_private_network_paths_and_single_activation(self):
        gateway = render("agentgateway", managed_values(), namespace="infra-agentgateway", release="infra-agentgateway")
        studio = render("studio/api", studio_values(), namespace="frontend-studio", release="frontend-studio-api")
        native = render("contextforge", native_values(), namespace="contextforge", release="contextforge")
        bao = render("openbao", {"mcp": {"studioSetup": {"enabled": True}}}, namespace="infra-openbao", release="infra-openbao")
        policy = resource(gateway, "NetworkPolicy", "contextforge-providers")["spec"]
        studio_policy = resource(studio, "NetworkPolicy", "frontend-studio-api-egress-network-policy")["spec"]
        native_policy = resource(native, "NetworkPolicy", "contextforge")["spec"]
        bao_policy = resource(bao, "NetworkPolicy", "studio-mcp")["spec"]
        controller_policy = resource(gateway, "NetworkPolicy", "infra-agentgateway-controller-network-policy")["spec"]
        studio_pod = resource(studio, "Deployment", "frontend-studio-api-deployment")["spec"]["template"]
        bao_pod = resource(bao, "StatefulSet", "infra-openbao")["spec"]["template"]
        native_pod = next(doc["spec"]["template"] for doc in documents(native) if doc["kind"] == "Deployment")
        controller_pod = next(doc["spec"]["template"] for doc in documents(gateway)
                              if doc["kind"] == "Deployment" and doc["spec"]["template"]["metadata"]["labels"].get("agentgateway"))
        provider_labels = {"gateway.networking.k8s.io/gateway-name": resource(gateway, "Gateway", "contextforge-providers")["metadata"]["name"]}

        def permits(rules, peer_key, namespace, labels, port):
            for rule in rules:
                if not any(item["port"] == port for item in rule.get("ports", [])):
                    continue
                for peer in rule.get(peer_key, []):
                    if peer.get("namespaceSelector", {}).get("matchLabels", {}).get("kubernetes.io/metadata.name", namespace) != namespace:
                        continue
                    if "ipBlock" not in peer and all(labels.get(key) == value for key, value in peer.get("podSelector", {}).get("matchLabels", {}).items()):
                        return True
            return False

        for destination, namespace, labels, port, source_policy, source_namespace, source_labels in (
            (bao_policy, "infra-openbao", bao_pod["metadata"]["labels"], 8200, studio_policy, "frontend-studio", studio_pod["metadata"]["labels"]),
            (policy, "infra-agentgateway", provider_labels, 15000, studio_policy, "frontend-studio", studio_pod["metadata"]["labels"]),
            (policy, "infra-agentgateway", provider_labels, 8080, native_policy, "contextforge", native_pod["metadata"]["labels"]),
            (controller_policy, "infra-agentgateway", controller_pod["metadata"]["labels"], "grpc-xds-agw", policy, "infra-agentgateway", provider_labels),
        ):
            self.assertTrue(all(labels.get(key) == value for key, value in destination["podSelector"]["matchLabels"].items()))
            self.assertTrue(all(source_labels.get(key) == value for key, value in source_policy["podSelector"]["matchLabels"].items()))
            self.assertTrue(permits(source_policy["egress"], "to", namespace, labels, port))
            self.assertTrue(permits(destination["ingress"], "from", source_namespace, source_labels, port))
        self.assertFalse(permits(policy["ingress"], "from", "frontend-studio", studio_pod["metadata"]["labels"], 8080))
        self.assertFalse(permits(policy["ingress"], "from", "contextforge", native_pod["metadata"]["labels"], 15000))
        self.assertFalse(permits(policy["ingress"], "from", "frontend-studio", {"app.kubernetes.io/name": "other"}, 15000))
        self.assertFalse(permits(bao_policy["ingress"], "from", "frontend-studio", studio_pod["metadata"]["labels"], 8203))
        config = json.loads(resource(native, "ConfigMap", "contextforge-setup-code")["data"]["config.json"])
        self.assertTrue(config["studioSetup"])
        self.assertTrue(config["adminDiscovery"]["enabled"])
        self.assertNotIn("freshInitialization", config)
        container = studio_pod["spec"]["containers"][0]
        env = {item["name"]: item.get("value") for item in container["env"]}
        self.assertEqual(env["K8S_STUDIO_CONTEXTFORGE_ADMIN_DISCOVERY_ENABLED"], "true")
        self.assertEqual(env["K8S_STUDIO_MCP_SETUP_ENABLED"], "true")
        self.assertEqual(env["K8S_STUDIO_CONTEXTFORGE_PUBLICATION_STATUS_PATH"], "/var/run/contextforge-setup/publication.json")
        self.assertEqual(env["K8S_STUDIO_MCP_OPENBAO_URL"], "https://infra-openbao.infra-openbao.svc.cluster.local:8200")
        self.assertEqual(env["K8S_STUDIO_MCP_KUBERNETES_URL"], "https://kubernetes.default.svc")
        self.assertEqual(env["K8S_STUDIO_MCP_PROVIDER_ADMIN_URL"], "http://contextforge-providers.infra-agentgateway.svc.cluster.local:15000")
        self.assertNotIn("K8S_STUDIO_MCP_OPENBAO_ROLE", env)
        self.assertEqual(studio_pod["spec"]["serviceAccountName"], "studio-mcp")
        self.assertFalse(studio_pod["spec"]["automountServiceAccountToken"])
        account = resource(studio, "ServiceAccount", studio_pod["spec"]["serviceAccountName"])
        self.assertFalse(account["automountServiceAccountToken"])
        volumes = {item["name"]: item for item in studio_pod["spec"]["volumes"]}
        identity = volumes["mcp-identity"]["projected"]
        self.assertEqual(identity["defaultMode"], 0o440)
        self.assertEqual(identity["sources"], [
            {"serviceAccountToken": {"audience": "openbao", "expirationSeconds": 600, "path": "openbao-token"}},
            {"serviceAccountToken": {"expirationSeconds": 600, "path": "kubernetes-token"}},
            {"configMap": {"name": "kube-root-ca.crt", "items": [{"key": "ca.crt", "path": "ca.crt"}]}}])
        mounts = {item["name"]: item for item in container["volumeMounts"]}
        self.assertEqual(mounts["mcp-identity"]["mountPath"], "/var/run/mcp-identity")
        for variable, name in (("K8S_STUDIO_MCP_OPENBAO_TOKEN_FILE", "openbao-token"),
                               ("K8S_STUDIO_MCP_KUBERNETES_TOKEN_FILE", "kubernetes-token"),
                               ("K8S_STUDIO_MCP_KUBERNETES_CA_CERT", "ca.crt")):
            self.assertEqual(env[variable], mounts["mcp-identity"]["mountPath"] + "/" + name)
        self.assertEqual(env["K8S_STUDIO_MCP_OPENBAO_CA_CERT"], mounts["contextforge-ca"]["mountPath"] + "/ca.crt")
        self.assertEqual(volumes["contextforge-ca"]["configMap"]["name"], "infra-openbao-ca-bundle")
        for name in ("mcp-identity", "contextforge-setup", "contextforge-ca"):
            self.assertTrue(mounts[name]["readOnly"])
            self.assertNotIn("subPath", mounts[name])
        for other in studio_pod["spec"]["containers"][1:] + studio_pod["spec"]["initContainers"]:
            self.assertNotIn("mcp-identity", {item["name"] for item in other.get("volumeMounts", [])})
        disabled = render("studio/api", namespace="frontend-studio", release="frontend-studio-api")
        disabled_pod = resource(disabled, "Deployment", "frontend-studio-api-deployment")["spec"]["template"]["spec"]
        self.assertNotIn("serviceAccountName", disabled_pod)
        self.assertFalse(any(item["name"].startswith("mcp-") for item in disabled_pod["volumes"]))
        api_destinations = [(peer["ipBlock"]["cidr"], rule["ports"][0]["port"])
                            for rule in studio_policy["egress"] for peer in rule["to"] if "ipBlock" in peer]
        self.assertEqual(api_destinations, [("192.0.2.1/32", 443), ("198.51.100.1/32", 6443)])


if __name__ == "__main__":
    unittest.main()
