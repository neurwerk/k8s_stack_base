"""Remote NER must not create broad trust or silently use an older Engine."""

import json
import unittest

from helm import render, resource


def remote_values():
    return {"monitorPiiEngine": {
        "image": "ghcr.io/neurwerk/k8s-stack-pii-engine:0.12.0-cpu",
        "analyzerBackend": "remote-gliner",
        "remote": {
            "models": [{
                "name": "multilingual", "kind": "gliner", "model_name": "ner-multilingual",
                "url": "https://ner.example.test/extract", "languages": ["en", "de"],
                "inference_threshold": 0.5,
                "apiKeySecretRef": {"name": "private-ner-credential", "key": "bearer-key"},
            }],
            "egress": [{"cidr": "192.0.2.10/32", "port": 443}],
        },
    }}


class PiiRemoteNerTests(unittest.TestCase):
    def test_remote_trust_is_scoped_and_credentials_are_mounted(self):
        rendered = render("pii-engine", remote_values())
        pod = resource(rendered, "Deployment")["spec"]["template"]["spec"]
        self.assertFalse(any(volume["name"] == "model-cache" for volume in pod["volumes"]))
        key = next(volume for volume in pod["volumes"] if volume["name"].startswith("remote-key-"))
        self.assertEqual(key["secret"]["secretName"], "private-ner-credential")
        config = json.loads(resource(rendered, "ConfigMap", "monitor-pii-engine-config-map")
                            ["data"]["remote.json"])
        self.assertNotIn("apiKeySecretRef", config["models"][0])
        self.assertTrue(config["models"][0]["api_key_file"].endswith("/api-key"))
        policy = resource(rendered, "NetworkPolicy", "monitor-pii-engine-runtime-network-policy")
        rules = [rule for rule in policy["spec"]["egress"]
                 if any("ipBlock" in peer for peer in rule["to"])]
        self.assertEqual(rules, [{"to": [{"ipBlock": {"cidr": "192.0.2.10/32"}}],
                                 "ports": [{"port": 443, "protocol": "TCP"}]}])

    def test_incompatible_image_and_unscoped_egress_are_rejected(self):
        values = remote_values()
        values["monitorPiiEngine"]["image"] = "ghcr.io/neurwerk/k8s-stack-pii-engine:0.11.0-cpu"
        with self.assertRaisesRegex(AssertionError, "compatible"):
            render("pii-engine", values)
        for cidr in ("0.0.0.0/0", "::/0", "10.0.0.0/8"):
            values = remote_values()
            values["monitorPiiEngine"]["remote"]["egress"][0]["cidr"] = cidr
            with self.subTest(cidr=cidr), self.assertRaisesRegex(AssertionError, "scoped"):
                render("pii-engine", values)

    def test_kserve_requires_read_only_tokenizer_assets(self):
        values = remote_values()
        values["monitorPiiEngine"]["analyzerBackend"] = "remote-kserve"
        values["monitorPiiEngine"]["remote"]["models"] = [{
            "name": "english", "kind": "kserve", "model_name": "ner-english",
            "url": "https://ner.example.test/v1/models/ner-english:predict", "languages": ["en"],
            "tokenizer_path": "/remote-tokenizers/english",
            "tokenizer_sha256": {"config.json": "0" * 64},
        }]
        with self.assertRaisesRegex(AssertionError, "tokenizer PVC"):
            render("pii-engine", values)
        values["monitorPiiEngine"]["remote"]["tokenizerClaimName"] = "ner-tokenizers"
        rendered = render("pii-engine", values)
        pod = resource(rendered, "Deployment")["spec"]["template"]["spec"]
        mount = next(item for item in pod["containers"][0]["volumeMounts"]
                     if item["name"] == "remote-tokenizers")
        self.assertTrue(mount["readOnly"])

    def test_local_mode_does_not_create_remote_trust(self):
        values = remote_values()
        values["monitorPiiEngine"]["analyzerBackend"] = "local"
        rendered = render("pii-engine", values)
        config = resource(rendered, "ConfigMap", "monitor-pii-engine-config-map")["data"]
        self.assertNotIn("remote.json", config)
        pod = resource(rendered, "Deployment")["spec"]["template"]["spec"]
        self.assertFalse(any(volume["name"].startswith("remote-") for volume in pod["volumes"]))
