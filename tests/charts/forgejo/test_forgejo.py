import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from helm import render as helm_render

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "charts/forgejo"
FIXTURE = Path(__file__).with_name("enabled.yaml")


def render(*overrides, enabled=True, success=True, json_overrides=()):
    args = []
    for override in overrides:
        args += ["--set", override]
    for override in json_overrides:
        args += ["--set-json", override]
    result = helm_render(
        "forgejo",
        release="forgejo",
        namespace="forgejo",
        value_files=(FIXTURE,) if enabled else (),
        extra_args=args,
        check=False,
    )
    if not success:
        assert result.returncode != 0, result.stdout
        return result.stderr
    assert result.returncode == 0, result.stderr
    documents = {}
    for doc in re.split(r"(?m)^---\s*$", result.stdout):
        kind = re.search(r"(?m)^kind: (\S+)$", doc)
        if kind:
            name = re.search(r"(?m)^metadata:\n  name: (\S+)$", doc)
            documents[kind[1], name[1]] = doc
    return documents


class RenderTests(unittest.TestCase):
    def test_disabled_renders_nothing(self):
        self.assertEqual(render(enabled=False), {})

    def test_private_contract(self):
        docs = render()
        self.assertNotIn(("Gateway", "forgejo-gateway"), docs)
        self.assertFalse({"Secret", "Namespace", "ExternalSecret"} & {k[0] for k in docs})
        cert = docs["Certificate", "forgejo-tls"]
        for field in [
            'dnsNames: ["forgejo.example.com"]',
            "algorithm: RSA",
            "size: 2048",
            "rotationPolicy: Always",
            "duration: 2160h",
            "name: letsencrypt-staging-cluster-issuer",
        ]:
            self.assertIn(field, cert)
        deploy = docs["Deployment", "forgejo"]
        images = re.findall(r"(?m)^\s+image: (\S+)$", deploy)
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0], images[1])
        for field in [
            "replicas: 1",
            "type: Recreate",
            '"/usr/bin/timeout", "-k", "10", "290"',
            "automountServiceAccountToken: false",
            'hostnames: ["forgejo.example.com"]',
            "readOnlyRootFilesystem: true",
            "scheme: HTTPS",
        ]:
            self.assertIn(field, deploy)
        self.assertRegex(deploy, r"livenessProbe:\s+tcpSocket:\s+port: web")
        init, app = deploy.split("      containers:\n", 1)
        app = app.split("      volumes:\n", 1)[0]
        self.assertNotIn("bootstrap-credentials", app)
        self.assertIn("bootstrap-credentials", init)
        cfg = docs["ConfigMap", "forgejo-config"]
        for field in [
            'FORGEJO__SERVER__LOCAL_ROOT_URL: "https://forgejo.example.com:3000/"',
            "FORGEJO__SERVER__SSH_SERVER_HOST_KEYS: /data/ssh/forgejo.ed25519",
            'FORGEJO__SERVICE__ENABLE_INTERNAL_SIGNIN: "false"',
            'FORGEJO__SERVICE__DISABLE_REGISTRATION: "false"',
            'FORGEJO__SERVICE__ALLOW_ONLY_EXTERNAL_REGISTRATION: "true"',
            "FORGEJO__OAUTH2_CLIENT__ACCOUNT_LINKING: disabled",
            "FORGEJO__DATABASE__SSL_MODE: disable",
            'FORGEJO__SECURITY__REVERSE_PROXY_TRUSTED_PROXIES: "127.0.0.0/8,::1/128"',
        ]:
            self.assertIn(field, cfg)
        self.assertIn(
            "helm.sh/resource-policy: keep", docs["PersistentVolumeClaim", "forgejo-data"]
        )
        self.assertIn(
            'storageClassName: "infra-rook-ceph-rbd"', docs["PersistentVolumeClaim", "forgejo-data"]
        )
        policy = docs["NetworkPolicy", "forgejo"]
        self.assertIn("ingress: []", policy)
        self.assertNotIn("ipBlock", policy)
        self.assertIn("port: 9712", policy)
        self.assertIn("port: 443", docs["Service", "forgejo"])

    def test_public_reuses_certificate_without_exposing_ssh(self):
        docs = render("externalGateway.enabled=true", "publicCertificates.useProduction=true")
        self.assertEqual(sum(kind == "Certificate" for kind, _ in docs), 1)
        gateway = docs["Gateway", "forgejo-gateway"]
        self.assertNotIn("cert-manager", gateway)
        self.assertNotIn("2222", gateway)
        self.assertIn("name: forgejo-tls", gateway)
        self.assertIn("name: letsencrypt-production-cluster-issuer", docs["Certificate", "forgejo-tls"])
        deploy = docs["Deployment", "forgejo"]
        self.assertNotIn("hostAliases", deploy)
        self.assertIn("scheme: HTTP\n", deploy)
        self.assertIn('FORGEJO__SERVER__LOCAL_ROOT_URL: "http://127.0.0.1:3000/"', docs["ConfigMap", "forgejo-config"])
        self.assertIn("port: 80", docs["HTTPRoute", "forgejo"])
        ingress = docs["NetworkPolicy", "forgejo"].split("  ingress:", 1)[1].split("  egress:", 1)[0]
        self.assertIn("port: 3000", ingress)
        self.assertNotIn("2222", ingress)

    def test_explicit_network_and_recovery(self):
        docs = render("canonicalEndpointRouting.mode=public-dns", "forgejo.networkPolicy.keycloakPublicCidrs[0]=203.0.113.9/32", "forgejo.networkPolicy.clients[0].namespace=integration", "forgejo.networkPolicy.clients[0].podSelector.app=consumer", "forgejo.recoveryLoginEnabled=true")
        policy = docs["NetworkPolicy", "forgejo"]
        self.assertIn('cidr: "203.0.113.9/32"', policy)
        self.assertIn("app: consumer", policy)
        self.assertIn("kubernetes.io/metadata.name: integration", policy)
        self.assertNotIn("app.kubernetes.io/name: traefik", policy)
        self.assertIn('FORGEJO__SERVICE__ENABLE_INTERNAL_SIGNIN: "true"', docs["ConfigMap", "forgejo-config"])

    def test_explicit_oidc_ca_selection(self):
        for public in ["false", "true"]:
            with self.subTest(public=public):
                defaults = render(f"externalGateway.enabled={public}")
                self.assertNotIn("name: oidc-ca", defaults["Deployment", "forgejo"])
                self.assertIn('OIDC_CA_FILE: ""', defaults["ConfigMap", "forgejo-config"])
                docs = render(f"externalGateway.enabled={public}", "forgejo.oidc.caConfigMap=keycloak-ca")
                deploy = docs["Deployment", "forgejo"]
                self.assertIn('configmap.reloader.stakater.com/reload: "forgejo-config,forgejo-scripts,keycloak-ca"', deploy)
                self.assertIn('OIDC_CA_FILE: "/run/oidc-ca/ca.crt"', docs["ConfigMap", "forgejo-config"])
                self.assertRegex(deploy, r'name: oidc-ca\s+configMap:\s+name: "keycloak-ca"\s+optional: false\s+items:\s+- key: ca.crt\s+path: ca.crt')
                self.assertIn("mountPath: /run/oidc-ca", deploy)
                self.assertNotIn(("ConfigMap", "keycloak-ca"), docs)
                self.assertIn('FORGEJO__SERVICE__ENABLE_INTERNAL_SIGNIN: "false"', docs["ConfigMap", "forgejo-config"])

    def test_private_https_peers_do_not_inherit_ssh_or_public_transport(self):
        peer = ("forgejo.networkPolicy.httpsClients[0].namespace=private-access",
                "forgejo.networkPolicy.httpsClients[0].podSelector.app=device-gateway")
        docs = render(*peer)
        policy = docs["NetworkPolicy", "forgejo"]
        ingress = policy.split("  ingress:", 1)[1].split("  egress:", 1)[0]
        self.assertEqual(ingress, '''
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: "private-access"
          podSelector:
            matchLabels:
              app: device-gateway
      ports:
        - port: 3000
          protocol: TCP
''')
        self.assertIn("port: 443", docs["Service", "forgejo"])
        self.assertNotIn(("Gateway", "forgejo-gateway"), docs)
        baseline = render()
        self.assertEqual({k: v for k, v in docs.items() if k[0] != "NetworkPolicy"},
                         {k: v for k, v in baseline.items() if k[0] != "NetworkPolicy"})
        self.assertEqual(policy.split("  egress:", 1)[1],
                         baseline["NetworkPolicy", "forgejo"].split("  egress:", 1)[1])
        self.assertIn("httpsClients requires externalGateway.enabled=false",
                      render(*peer, "externalGateway.enabled=true", success=False))
        self.assertEqual(render(*peer, enabled=False), {})
        combined = render(*peer, "forgejo.networkPolicy.clients[0].namespace=integration",
                          "forgejo.networkPolicy.clients[0].podSelector.app=consumer")
        ingress = combined["NetworkPolicy", "forgejo"].split("  ingress:", 1)[1].split("  egress:", 1)[0]
        self.assertEqual(ingress.count("port: 2222"), 1)
        self.assertEqual(ingress.count("port: 3000"), 2)
        for field, value, diagnostic in [
            ("podSelector", {}, "minProperties"),
            ("namespace", "*", "does not match pattern"),
            ("ports", [2222], "additional properties"),
        ]:
            with self.subTest(field=field):
                invalid = {"namespace": "private-access", "podSelector": {"app": "device-gateway"}, field: value}
                error = render(success=False, json_overrides=[
                    "forgejo.networkPolicy.httpsClients=" + json.dumps([invalid])])
                self.assertIn(diagnostic, error)

    def test_rejects_unsupported_security_configuration(self):
        for setting in [
            "forgejo.hostname=", "forgejo.hostname=https://forgejo.example.com",
            "authKeycloak.hostname=", "authKeycloak.realm=../other",
            "forgejo.enabled=unsafe", "forgejo.replicas=2",
            "forgejo.config.service.ENABLE_INTERNAL_SIGNIN=true",
            "forgejo.database.type=sqlite3", "forgejo.runners.enabled=true",
            "externalGateway.port=2222", "canonicalEndpointRouting.mode=unknown",
            "canonicalEndpointRouting.mode=public-dns",
            "forgejo.networkPolicy.keycloakPublicCidrs[0]=0.0.0.0/0",
            "forgejo.networkPolicy.clients[0].namespace=integration",
            "forgejo.networkPolicy.httpsClients[0].namespace=private-access",
            "forgejo.networkPolicy.httpsClients[0].podSelector.app=device-gateway",
            "forgejo.oidc.caConfigMap=invalid/name", "forgejo.oidc.verify=false",
        ]:
            with self.subTest(setting=setting):
                render(setting, success=False)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ["data", "config", "secrets", "tls", "bin"]:
            (self.root / name).mkdir()
        self.state = self.root / "state.json"
        self.state.write_text(json.dumps({"auth": [], "users": [], "calls": []}))
        self.env = dict(os.environ, MOCK_STATE=str(self.state), PATH=f"{self.root}/bin:{os.environ['PATH']}", OIDC_CA_FILE="", OIDC_DISCOVERY_URL="https://forgejo.example.com/realms/test/.well-known/openid-configuration", RECOVERY_EMAIL="forgejo-recovery@forgejo.example.com")
        self.env["FORGEJO__SERVICE__ENABLE_INTERNAL_SIGNIN"] = "false"
        for name in ["dbPassword", "oidcClientSecret", "secretKey", "internalToken", "oauth2JwtSecret", "lfsJwtSecret", "adminPassword"]:
            value = "A" * 43 if "JwtSecret" in name else "synthetic ; $(false) ' \" value"
            (self.root / "secrets" / name).write_text(value)
        (self.root / "tls/tls.crt").write_text("synthetic certificate\n")
        (self.root / "ca.pem").write_text("synthetic CA\n")
        cli = self.root / "bin/cli"
        cli.write_text(f"#!{sys.executable}\n" + Path(__file__).with_name("fake_cli.py").read_text())
        cli.chmod(0o700)
        converter = self.root / "bin/environment-to-ini"
        converter.write_text('#!/bin/sh\ntest ! -s "$2" || exit 1\nprintf "fresh configuration\\n" > "$4"\n')
        converter.chmod(0o700)
        keygen = self.root / "bin/ssh-keygen"
        keygen.write_text('#!/bin/sh\nfor last; do :; done\nprintf "persistent key\\n" > "$last"\n')
        keygen.chmod(0o700)
        script = (CHART / "files/bootstrap.sh").read_text()
        for source, target in [("/app/gitea/gitea", str(cli)), ("/run/secrets/forgejo", str(self.root / "secrets")), ("/run/bootstrap", str(self.root / "secrets")), ("/run/forgejo", str(self.root / "config")), ("/run/tls", str(self.root / "tls")), ("/data", str(self.root / "data")), ("/etc/ssl/certs/ca-certificates.crt", str(self.root / "ca.pem"))]:
            script = script.replace(source, target)
        self.script = self.root / "bootstrap.sh"
        self.script.write_text(script)

    def run_bootstrap(self, success=True):
        result = subprocess.run(["bash", str(self.script)], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        self.assertNotIn("synthetic ;", result.stdout + result.stderr)
        return json.loads(self.state.read_text())

    def test_restart_preserves_recovery_password_and_host_key(self):
        first = self.run_bootstrap()
        key = self.root / "data/ssh/forgejo.ed25519"
        before = key.stat().st_mtime_ns
        (self.root / "config/app.ini").write_text("stale setting\n")
        second = self.run_bootstrap()
        self.assertEqual(key.stat().st_mtime_ns, before)
        self.assertNotIn("stale", (self.root / "config/app.ini").read_text())
        self.assertEqual(first["users"], second["users"])
        calls = second["calls"]
        self.assertEqual(sum(c[:3] == ["admin", "user", "create"] for c in calls), 1)
        self.assertEqual(sum(c[:3] == ["admin", "auth", "update-oauth"] for c in calls), 1)
        oauth = next(c for c in calls if c[:3] == ["admin", "auth", "add-oauth"])
        self.assertEqual(oauth[oauth.index("--secret") + 1], (self.root / "secrets/oidcClientSecret").read_text())
        self.assertIn("--allow-username-change=false", oauth)
        self.assertEqual(oauth[oauth.index("--required-claim-value") + 1], "forgejo-user")
        self.assertEqual(oauth[oauth.index("--admin-group") + 1], "forgejo-admin")

    def test_exact_username_not_prefix(self):
        state = json.loads(self.state.read_text())
        state["users"] = ["1 forgejo-recovery-other synthetic true true false"]
        self.state.write_text(json.dumps(state))
        result = self.run_bootstrap()
        self.assertEqual(len(result["users"]), 2)

    def test_failure_blocks_startup_without_deleting_sources(self):
        for failure in ["migrate", "auth-list", "user-list", "auth-write", "user-write", "auth-readback", "user-readback"]:
            with self.subTest(failure=failure):
                self.state.write_text(json.dumps({"auth": [], "users": [], "calls": []}))
                self.env["MOCK_FAILURE"] = failure
                state = self.run_bootstrap(success=False)
                self.assertFalse(any("delete" in call for call in state["calls"]))

    def test_rejects_unexpected_sources_and_invalid_recovery_identity(self):
        for auth, users in [(["1 | keycloak-other | OAuth2 | true"], []), (["1 | keycloak | LDAP | true"], []), (["1 | keycloak | OAuth2 | false"], []), ([], ["1 forgejo-recovery synthetic true false false"])]:
            with self.subTest(auth=auth, users=users):
                self.state.write_text(json.dumps({"auth": auth, "users": users, "calls": []}))
                self.run_bootstrap(success=False)

    def test_malformed_jwt_secret_fails_before_migration(self):
        (self.root / "secrets/oauth2JwtSecret").write_text("invalid")
        state = self.run_bootstrap(success=False)
        self.assertEqual(state["calls"], [])

    def test_recovery_skips_failed_oauth_update_only_for_existing_install(self):
        provisioned = self.run_bootstrap()
        self.env["MOCK_FAILURE"] = "auth-write"
        normal = self.run_bootstrap(success=False)
        self.assertEqual(normal["calls"][-1][:3], ["admin", "auth", "update-oauth"])
        self.env["FORGEJO__SERVICE__ENABLE_INTERNAL_SIGNIN"] = "true"
        recovered = self.run_bootstrap()
        calls = recovered["calls"][len(normal["calls"]):]
        self.assertEqual(calls[0], ["migrate"])
        self.assertEqual(calls[1:], [["admin", "auth", "list", "--vertical-bars"], ["admin", "user", "list"], ["admin", "user", "list"]])
        self.assertEqual(recovered["auth"], provisioned["auth"])
        self.assertEqual(recovered["users"], provisioned["users"])

    def test_recovery_requires_existing_enabled_source_and_valid_admin(self):
        self.env["FORGEJO__SERVICE__ENABLE_INTERNAL_SIGNIN"] = "true"
        source = ["1 | keycloak | OAuth2 | true"]
        admin = ["1 forgejo-recovery synthetic true true false"]
        for auth, users in [
            ([], []), ([], admin), (source, []),
            (["1 | keycloak | OAuth2 | false"], admin),
            (source * 2, admin),
            (source, ["1 forgejo-recovery synthetic false true false"]),
            (source, ["1 forgejo-recovery synthetic true false false"]),
        ]:
            with self.subTest(auth=auth, users=users):
                self.state.write_text(json.dumps({"auth": auth, "users": users, "calls": []}))
                result = self.run_bootstrap(success=False)
                self.assertEqual(result["auth"], auth)
                self.assertEqual(result["users"], users)
                self.assertTrue(all(call == ["migrate"] or call[:3] in [["admin", "auth", "list"], ["admin", "user", "list"]] for call in result["calls"]))

    def test_oidc_trust_is_independent_and_rebuilt(self):
        self.run_bootstrap()
        bundle = self.root / "config/ca-bundle.pem"
        self.assertEqual(bundle.read_text(), "synthetic CA\n")
        ca = self.root / "oidc-ca.pem"
        ca.write_text("independent identity CA\n")
        self.env["OIDC_CA_FILE"] = str(ca)
        self.run_bootstrap()
        self.assertEqual(bundle.read_text(), "synthetic CA\n\nindependent identity CA\n")
        self.assertNotIn("synthetic certificate", bundle.read_text())
        self.env["OIDC_CA_FILE"] = ""
        self.run_bootstrap()
        self.assertEqual(bundle.read_text(), "synthetic CA\n")

    def test_selected_oidc_ca_missing_or_empty_blocks_migration(self):
        ca = self.root / "oidc-ca.pem"
        self.env["OIDC_CA_FILE"] = str(ca)
        self.assertEqual(self.run_bootstrap(success=False)["calls"], [])
        ca.write_text("")
        self.assertEqual(self.run_bootstrap(success=False)["calls"], [])


if __name__ == "__main__":
    unittest.main()
