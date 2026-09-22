"""Static gateway rendering; actual packet checks run separately on isolated CI."""

import unittest
import subprocess

from helm import ROOT, render, resources


class WireguardTests(unittest.TestCase):
    def test_optional_secret_delivery_is_exact_and_unselected(self):
        package = subprocess.check_output(
            ["kustomize", "build", str(ROOT / "releases/wireguard/secret-sync")], text=True
        )
        for text in ["name: wireguard-external-secrets", "name: wireguard-openbao-secret-store",
                     "name: wireguard-server-key", "key: wireguard/internal", "property: privateKey",
                     "audiences:", "- openbao", "deletionPolicy: Retain"]:
            self.assertIn(text, package)
        self.assertEqual(package.count("secretKey:"), 1)
        self.assertNotIn("kind: Secret\n", package)
        namespace = subprocess.check_output(
            ["kustomize", "build", str(ROOT / "releases/namespaces/wireguard")], text=True
        )
        self.assertIn('secrets.neurwerk.com/openbao-trust: "true"', namespace)
        for stage in ["namespaces", "infrastructure", "applications"]:
            output = subprocess.check_output(
                ["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone",
                 str(ROOT / "releases" / stage)], text=True
            )
            self.assertNotIn("wireguard", output)

    def test_static_boundary_and_empty_recovery(self):
        self.assertEqual(render("wireguard", {"wireguard": {"enabled": False}}).stdout.strip(), "")
        empty = render("wireguard", {})
        config = resources(empty, "ConfigMap")[0]
        self.assertEqual(config.count("policy drop;"), 3)
        self.assertNotIn("[Peer]", config)
        self.assertNotIn("masquerade", config)
        self.assertLess(config.index("nft -f"), config.index('ip link set wg0 mtu "$MTU" up'))
        deployment = resources(empty, "Deployment")[0]
        for text in ["replicas: 0", "type: Recreate", "automountServiceAccountToken: false",
                     "readOnlyRootFilesystem: true", "add: [NET_ADMIN]", "drop: [ALL]",
                     "secretName: wireguard-server-key"]:
            self.assertIn(text, deployment)
        for forbidden in ["hostNetwork:", "hostPath:", "SYS_MODULE", "reloader", "checksum/"]:
            self.assertNotIn(forbidden, deployment)
        peer = {"owner": "synthetic", "address": "192.0.2.2", "publicKey": "A" * 43 + "="}
        enabled = render("wireguard", {"wireguard": {"peers": [peer], "replicas": 1}})
        config = resources(enabled, "ConfigMap")[0]
        self.assertIn("AllowedIPs = 192.0.2.2/32", config)
        self.assertIn("ct original ip daddr 192.0.2.10 ct original proto-dst 443 accept", config)
        self.assertIn("iifname \"wg0\" ip saddr 192.0.2.2 ip daddr 192.0.2.10 tcp dport 443 dnat ip to 198.51.100.10:443", config)
        policy = resources(enabled, "NetworkPolicy")[0]
        self.assertIn("kubernetes.io/metadata.name: forgejo", policy)
        self.assertIn("app.kubernetes.io/instance: forgejo", policy)
        self.assertIn("port: 3000", policy)
        self.assertNotIn("port: 443", policy)
        self.assertNotIn("2222", policy)

    def test_rejects_unsafe_or_incomplete_inputs(self):
        for values, diagnostic in [
            ({"service": {"type": "NodePort"}}, "explicit port"),
            ({"virtualIP": "198.51.100.10"}, "must differ"),
        ]:
            with self.subTest(values=values):
                result = render("wireguard", {"wireguard": values}, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(diagnostic, result.stderr)
