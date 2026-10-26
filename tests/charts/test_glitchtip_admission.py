"""Protect the outer identity and transport boundaries, not native token ownership."""

import json
import re
import subprocess
import unittest

import yaml

from helm import ROOT, documents, env_value, render, resource


GROUP = "/access/neurwerk-glitchtip-admins"


def settings():
    return {"glitchtip": {
        "enabled": True, "mcpEnabled": True,
        "admission": {"issuerURL": "https://auth.example.com/realms/example", "issuerCIDRs": ["192.0.2.10/32"]},
        "gateway": {"ui": {"enabled": True, "tailscale": {"hostname": "glitchtip-example", "tags": ["tag:glitchtip-ui"]}}},
    }}


class GlitchTipAdmissionTests(unittest.TestCase):
    def test_default_off_never_exposes_or_consumes_admission_credentials(self):
        self.assertFalse(documents(render("glitchtip", {"glitchtip": {"enabled": False}})))

    def test_missing_identity_and_bypass_configuration_fail_before_rendering(self):
        for name, value in (
            ("issuerURL", ""), ("issuerURL", "http://auth.example.com/realms/example"),
            ("allowedGroup", "/access/neurwerk-platform-admins"), ("allowedGroup", ""),
            ("clientID", "studio"), ("secret", ""), ("issuerCIDRs", []),
            ("skipAuthRoutes", ["/"]), ("skipJwtBearerTokens", True), ("cookieRefresh", "1m"),
            ("sslUpstreamInsecureSkipVerify", True),
        ):
            with self.subTest(name=name, value=value):
                values = settings()
                values["glitchtip"]["admission"][name] = value
                result = render("glitchtip", values, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_admission_has_no_native_identity_or_bearer_shortcut(self):
        result = render("glitchtip", settings(), namespace="glitchtip")
        pod = resource(result, "Deployment", "glitchtip-boundary")["spec"]["template"]["spec"]
        admission = next(c for c in pod["containers"] if c["name"] == "browser")
        args = admission["args"]
        for argument in ("--cookie-expire=15m", "--cookie-refresh=0",
                         "--skip-jwt-bearer-tokens=false", "--session-cookie-minimal=true",
                         "--redirect-url=https://glitchtip.example.com/oauth2/callback"):
            self.assertIn(argument, args)
        self.assertFalse(any("skip-auth" in arg or "trusted-ip" in arg or "insecure" in arg for arg in args))
        config = yaml.safe_load(resource(result, "ConfigMap", "glitchtip-boundary")["data"]["browser.yaml"])
        self.assertEqual(config["providers"][0]["allowedGroups"], [GROUP])
        self.assertFalse(config["providers"][0]["oidcConfig"]["insecureSkipNonce"])
        self.assertTrue(all(not header.get("values") and header["preserveRequestValue"] is False
                            for header in config["injectRequestHeaders"] if header["name"] != "X-Forwarded-Proto"))
        scheme = next(header for header in config["injectRequestHeaders"] if header["name"] == "X-Forwarded-Proto")
        self.assertEqual(scheme["values"], [{"secretSource": {"fromEnv": "GLITCHTIP_PROXY_SCHEME"}}])
        self.assertTrue(all(c["image"] == admission["image"] for c in pod["containers"]))
        for variable in admission["env"]:
            if "COOKIE_SECRET" in variable["name"]:
                self.assertEqual(variable["valueFrom"]["secretKeyRef"]["name"], "glitchtip-admission")
                self.assertNotIn("value", variable)
        self.assertNotIn("glitchtip-owner", yaml.safe_dump(pod))
        self.assertFalse(pod["automountServiceAccountToken"])

    def test_native_listener_has_only_boundary_peer_and_managed_listener_has_only_contextforge(self):
        result = render("glitchtip", settings(), namespace="glitchtip")
        native = resource(result, "NetworkPolicy", "glitchtip-application")["spec"]["ingress"]
        self.assertEqual(len(native), 1)
        peer = native[0]["from"][0]
        self.assertEqual(peer["namespaceSelector"]["matchLabels"], {"kubernetes.io/metadata.name": "glitchtip"})
        self.assertEqual(peer["podSelector"]["matchLabels"]["app.kubernetes.io/component"], "boundary")
        self.assertEqual(native[0]["ports"], [{"protocol": "TCP", "port": 8443}])
        boundary = resource(result, "NetworkPolicy", "glitchtip-boundary")["spec"]
        managed = next(rule for rule in boundary["ingress"] if rule["ports"][0]["port"] == 9443)
        self.assertEqual(managed["from"], [{
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "contextforge"}},
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "contextforge",
                "app.kubernetes.io/instance": "contextforge", "app.kubernetes.io/component": "application"}},
        }])
        def admits(rules, namespace, labels, port):
            return any(
                any(p["port"] == port for p in rule["ports"])
                and any(
                    peer.get("namespaceSelector", {}).get("matchLabels", {}).get(
                        "kubernetes.io/metadata.name", "glitchtip"
                    ) == namespace
                    and all(labels.get(key) == value for key, value in
                            peer.get("podSelector", {}).get("matchLabels", {}).items())
                    for peer in rule["from"]
                )
                for rule in rules
            )
        labels = managed["from"][0]["podSelector"]["matchLabels"]
        self.assertTrue(admits(boundary["ingress"], "contextforge", labels, 9443))
        self.assertFalse(admits(boundary["ingress"], "unrelated", labels, 9443))
        self.assertFalse(admits(boundary["ingress"], "contextforge", {"app": "unrelated"}, 9443))
        self.assertFalse(admits(native, "contextforge", labels, 8443))
        self.assertFalse(admits(native, "glitchtip", {"app": "unrelated"}, 8443))
        services = {doc["metadata"]["name"]: doc for doc in documents(result) if doc["kind"] == "Service"}
        self.assertEqual(services["glitchtip-mcp"]["spec"]["ports"], [{"name": "mcp", "port": 8443, "targetPort": "managed-mcp"}])
        for name in ("glitchtip-mcp", "glitchtip-ui", "glitchtip"):
            self.assertEqual(services[name]["spec"]["selector"]["app.kubernetes.io/component"], "boundary")
        self.assertTrue(all(doc["kind"] not in ("Ingress", "HTTPRoute", "Gateway") for doc in documents(result)))

    def test_scoped_internal_paths_and_verified_tls_are_separate_from_browser_gate(self):
        result = render("glitchtip", settings(), namespace="glitchtip")
        configs = {name: yaml.safe_load(config) for name, config in
                   resource(result, "ConfigMap", "glitchtip-boundary")["data"].items()}
        browser = configs["browser.yaml"]["upstreamConfig"]["upstreams"]
        self.assertTrue(next(upstream for upstream in browser if upstream["path"] == "/mcp")["static"])
        self.assertTrue(next(upstream for upstream in browser if upstream["path"] == "/mcp/")["static"])
        managed = configs["mcp.yaml"]["upstreamConfig"]["upstreams"]
        self.assertEqual({upstream["path"] for upstream in managed if not upstream.get("static")}, {
            "/mcp", "/mcp/token", "/mcp/register", "/mcp/revoke",
            "/.well-known/oauth-authorization-server", "/.well-known/oauth-authorization-server/mcp",
            "/.well-known/oauth-protected-resource/mcp",
        })
        self.assertEqual(managed[-1], {"id": "deny", "path": "/", "static": True, "staticCode": 404})
        for config in configs.values():
            for upstream in config["upstreamConfig"]["upstreams"]:
                if not upstream.get("static"):
                    self.assertEqual(upstream["uri"], "https://glitchtip-backend.glitchtip.svc.cluster.local:8443")
                    self.assertFalse(upstream.get("insecureSkipTLSVerify", False))
        cert = resource(result, "Certificate", "glitchtip-native")["spec"]
        self.assertEqual(cert["dnsNames"], ["glitchtip-mcp.glitchtip.svc.cluster.local", "glitchtip-backend.glitchtip.svc.cluster.local"])

    def test_tailnet_transport_matches_both_proxy_service_and_ingress_not_native(self):
        result = render("glitchtip", settings(), namespace="glitchtip", release="glitchtip")
        output = subprocess.run(
            ["kustomize", "build", str(ROOT / "releases/glitchtip/tailscale-access")],
            check=True, capture_output=True, text=True,
        )
        egress = next(d for d in yaml.safe_load_all(output.stdout) if d["kind"] == "NetworkPolicy")
        service = resource(result, "Service", "glitchtip-ui")["spec"]
        rule = egress["spec"]["egress"][0]
        peer = rule["to"][0]
        self.assertEqual(peer["namespaceSelector"]["matchLabels"], {"kubernetes.io/metadata.name": "glitchtip"})
        self.assertEqual(peer["podSelector"]["matchLabels"], service["selector"])
        self.assertEqual(service["ports"][0]["targetPort"], "admission-https")
        self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": 8443}])
        incoming = resource(result, "NetworkPolicy", "glitchtip-boundary")["spec"]["ingress"]
        tailnet = next(r for r in incoming if r["ports"] == rule["ports"])
        self.assertEqual(tailnet["from"][0]["namespaceSelector"]["matchLabels"], {"kubernetes.io/metadata.name": egress["metadata"]["namespace"]})
        self.assertEqual(tailnet["from"][0]["podSelector"], egress["spec"]["podSelector"])
        native = resource(result, "Service", "glitchtip-backend")["spec"]["selector"]
        self.assertNotEqual(peer["podSelector"]["matchLabels"], native)

    def test_status_and_ingestion_native_upstreams_match_exact_paths_not_prefixes(self):
        values = settings()
        values["glitchtip"]["gateway"]["status"] = {"enabled": True, "pagePath": "/status-pages/example/public/"}
        result = render("glitchtip", values)
        config = yaml.safe_load(resource(result, "ConfigMap", "glitchtip-boundary")["data"]["ingest.yaml"])
        upstreams = [u for u in config["upstreamConfig"]["upstreams"] if not u.get("static")]
        for path in ("/status-pages/example/public/", "/api/1/envelope/", "/api/24/store/"):
            self.assertEqual(sum(bool(re.fullmatch(u["path"], path)) for u in upstreams), 1)
        for path in ("/status-pages/example/public/extra", "/api/1/envelope/extra", "/api/0/", "/api/1/store//"):
            self.assertFalse(any(re.search(u["path"], path) for u in upstreams))
        self.assertTrue(all(u.get("rewriteTarget") == "$1" for u in upstreams))

    def test_dedicated_invocation_grant_cannot_leak_to_general_groups(self):
        role = "mcp:glitchtip:invoke"
        values = {"authKeycloak": {"agentgatewayClientRoles": ["llm:invoke", role],
            "agentgatewayAccessGroups": {GROUP: ["llm:invoke", role]}}}
        result = render("keycloak/realm-config/realm-roles", values)
        groups = json.loads(env_value(result, "KC_ACCESS_GROUPS"))
        self.assertEqual(groups[GROUP], {"realmRoles": ["glitchtip-admin"], "clientRoles": {"agentgateway": ["llm:invoke", role]}})
        composites = json.loads(env_value(result, "KC_REALM_ROLE_COMPOSITES"))
        self.assertNotIn("glitchtip-admin", composites["platform-admin"])
        for group in ("/access/neurwerk-mcp-all-users", "/access/neurwerk-platform-admins"):
            values["authKeycloak"]["agentgatewayAccessGroups"] = {group: ["llm:invoke", role]}
            self.assertNotEqual(render("keycloak/realm-config/realm-roles", values, check=False).returncode, 0)
