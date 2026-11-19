"""Keep database OAuth independent of the unfinished Vault integration."""

import unittest
from pathlib import Path
import ssl
import subprocess
import tempfile
import types
from unittest.mock import patch

from helm import render, resource
from test_mcp_credentials import native_values


class ContextForgeTests(unittest.TestCase):
    def test_extra_ca_trust_preserves_existing_roots_and_rejects_invalid_certificates(self):
        values = native_values()
        values["contextforge"].update(extraCaConfigMaps=["provider-ca-v1"], dnsSearches=["example.internal"])
        output = render("contextforge", values, namespace="contextforge", release="contextforge", value_files=())
        scripts = []
        for kind, name in (("Deployment", "contextforge"), ("Job", "contextforge-migration"),
                           ("Job", "contextforge-setup")):
            pod = resource(output, kind, name)["spec"]["template"]["spec"]
            volume = next(v for v in pod["volumes"] if v["name"] == "extra-ca-0")
            self.assertEqual(volume["configMap"], {"name": "provider-ca-v1", "items": [{"key": "ca.crt", "path": "ca.crt"}]})
            init = next(c for c in pod["initContainers"] if c["name"] == "prepare-ca-trust")
            self.assertIn({"name": "extra-ca-0", "mountPath": "/extra-ca/0", "readOnly": True}, init["volumeMounts"])
            scripts.append(init["args"][0])
        self.assertEqual(len(set(scripts)), 1)
        self.assertEqual(resource(output, "Deployment")["spec"]["template"]["spec"]["dnsConfig"]["searches"], ["example.internal"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("public", "openbao-ca", "extra-ca/0", "trust"):
                (root / name).mkdir(parents=True)
            for name in ("public", "openbao-ca", "extra-ca/0"):
                subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                                "-keyout", str(root / name / "key.pem"), "-out", str(root / name / "ca.crt"),
                                "-days", "1", "-subj", "/CN=" + name.replace("/", "-")],
                               check=True, capture_output=True)
            paths = lambda path: root / str(path).lstrip("/")
            certifi = types.SimpleNamespace(where=lambda: "/public/ca.crt")
            with patch.dict("sys.modules", {"certifi": certifi}), patch("pathlib.Path", side_effect=paths):
                exec(scripts[0], {})
            bundle = root / "trust/ca.crt"
            certificates = ssl.create_default_context(cafile=str(bundle)).get_ca_certs(binary_form=True)
            for name in ("public", "openbao-ca", "extra-ca/0"):
                self.assertIn(ssl.PEM_cert_to_DER_cert((root / name / "ca.crt").read_text()), certificates)
            (root / "extra-ca/0/ca.crt").write_text("invalid certificate")
            with patch.dict("sys.modules", {"certifi": certifi}), patch("pathlib.Path", side_effect=paths):
                with self.assertRaises(ssl.SSLError):
                    exec(scripts[0], {})

    def test_invalid_extra_ca_configuration_fails_rendering(self):
        for names in ("not-a-list", [""], ["bad/name"], ["same", "same"], [123]):
            with self.subTest(names=names):
                result = render("contextforge", {"contextforge": {"extraCaConfigMaps": names}}, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("extraCaConfigMaps", result.stderr)

    def test_database_oauth_never_requires_vault_token_with_or_without_proxy(self):
        for proxy in (False, True):
            with self.subTest(proxy=proxy):
                documents = render("contextforge", {
                    "contextforge": {
                        "trustedProxy": {
                            "enabled": proxy,
                            "studioOrigin": "https://studio.example.com",
                            "defaultUserRole": "reader",
                            "defaultTeamMemberRole": "member",
                        },
                    },
                }, namespace="contextforge", value_files=())
                for kind in ("Deployment", "Job"):
                    container = resource(documents, kind)["spec"]["template"]["spec"]["containers"][0]
                    environment = {entry["name"]: entry for entry in container["env"]}
                    self.assertEqual(environment["OAUTH_TOKEN_BACKEND"]["value"], "database")
                    self.assertNotIn("VAULT_TOKEN", environment)
                    self.assertIn("AUTH_ENCRYPTION_SECRET", environment)
