"""Canonical NER must isolate legacy selectors, mounts and remote trust."""

import copy
import unittest

import yaml

from helm import LINT_VALUES, ROOT, render, resource


def ner_values(mode="local"):
    # Synthetic compatibility fixture only, not a published platform image pin.
    ner = {"mode": mode}
    if mode != "disabled":
        ner.update(languageModels={"en": "english"},
                   models={"english": {"profile": "spacy-en-sm-v1"}})
    if mode == "remote":
        ner.update(languageModels={"en": "multilingual", "de": "multilingual"}, models={
            "multilingual": {
                "profile": "gliner-multilingual-pii-v1",
                "endpoint": "https://ner.example.com/extract",
                "inferenceThreshold": 0.45,
                "apiKeySecretRef": {"name": "ner-credential", "key": "bearer"},
            }}, egress=[{"cidr": "192.0.2.10/32", "port": 443}])
    return {"monitorPiiEngine": {
        "image": "ghcr.io/neurwerk/k8s-stack-pii-engine:0.13.0-cpu", "ner": ner,
    }}


class PiiNerLayersTests(unittest.TestCase):
    def test_canonical_modes_remove_legacy_selectors_and_cache(self):
        for mode in ("local", "disabled", "remote"):
            with self.subTest(mode=mode):
                rendered = render("pii-engine", ner_values(mode), value_files=(
                    LINT_VALUES, ROOT / "releases/shared/default-pii-settings.yaml"))
                pod = resource(rendered, "Deployment")["spec"]["template"]["spec"]
                env = {item["name"]: item.get("value") for item in pod["containers"][0]["env"]}
                self.assertEqual(env["PII_ENGINE_NER_CONFIG"], "/etc/pii-engine/ner.yaml")
                self.assertEqual(env["PII_ENGINE_DEVICE"], "cpu")
                forbidden = ("PII_ENGINE_ANALYZER_BACKEND", "PII_ENGINE_REMOTE_",
                             "PII_ENGINE_MODEL_")
                self.assertFalse(any(name.startswith(forbidden) for name in env))
                self.assertNotIn("model-cache", {item["name"] for item in pod["volumes"]})
                if mode != "remote":
                    self.assertFalse(any(item["name"].startswith("remote-") for item in pod["volumes"]))
                data = resource(rendered, "ConfigMap", "monitor-pii-engine-config-map")["data"]
                self.assertNotIn("remote.json", data)
                self.assertNotIn("ner", yaml.safe_load(data["policy.yaml"])["pii"])
                self.assertEqual(yaml.safe_load(data["ner.yaml"])["mode"], mode)

    def test_multilingual_one_model_secret_and_remote_capacity(self):
        rendered = render("pii-engine", ner_values("remote"))
        data = resource(rendered, "ConfigMap", "monitor-pii-engine-config-map")["data"]
        ner = yaml.safe_load(data["ner.yaml"])
        self.assertEqual(len(ner["models"]), 1)
        self.assertEqual(ner["languageModels"], {"en": "multilingual", "de": "multilingual"})
        self.assertNotIn("egress", ner)
        self.assertNotIn("tokenizerClaimName", ner)
        model = ner["models"]["multilingual"]
        self.assertNotIn("apiKeySecretRef", model)
        self.assertEqual(model["apiKeyFile"], "/var/run/pii-engine/remote/multilingual/api-key")
        self.assertEqual(ner["capacity"], {"callTimeout": 10, "maxCalls": 2048,
                                         "maxResponseBytes": 2097152, "maxConcurrentCalls": 1})
        pod = resource(rendered, "Deployment")["spec"]["template"]["spec"]
        key = next(item for item in pod["volumes"] if item["name"].startswith("remote-key-"))
        self.assertEqual(key["secret"]["secretName"], "ner-credential")
        self.assertEqual(key["secret"]["items"], [{"key": "bearer", "path": "api-key"}])
        mount = next(item for item in pod["containers"][0]["volumeMounts"]
                     if item["name"] == key["name"])
        self.assertTrue(mount["readOnly"])
        policy = resource(rendered, "NetworkPolicy", "monitor-pii-engine-runtime-network-policy")
        rules = [rule for rule in policy["spec"]["egress"]
                 if any("ipBlock" in peer for peer in rule["to"])]
        self.assertEqual(rules, [{"to": [{"ipBlock": {"cidr": "192.0.2.10/32"}}],
                                  "ports": [{"port": 443, "protocol": "TCP"}]}])

    def test_mixed_remote_uses_readonly_tokenizers_and_identity_passthrough(self):
        values = ner_values("remote")
        ner = values["monitorPiiEngine"]["ner"]
        ner["languageModels"]["en"] = "english"
        ner["models"]["english"] = {
            "profile": "kserve-en-openpii-v1", "modelName": "ner-english",
            "endpoint": "https://ner.example.com/v1/models/ner-english:predict",
            "tokenizerPath": "/remote-tokenizers/english",
        }
        with self.assertRaisesRegex(AssertionError, "tokenizer PVC"):
            render("pii-engine", values)
        ner["tokenizerClaimName"] = "ner-tokenizers"
        ner["models"]["multilingual"].update(upstream="urchade/gliner_multi_pii-v1",
                                            revision="operator-verified-revision")
        rendered = render("pii-engine", values)
        pod = resource(rendered, "Deployment")["spec"]["template"]["spec"]
        tokenizer = next(item for item in pod["volumes"] if item["name"] == "remote-tokenizers")
        self.assertEqual(tokenizer["persistentVolumeClaim"]["claimName"], "ner-tokenizers")
        mount = next(item for item in pod["containers"][0]["volumeMounts"]
                     if item["name"] == "remote-tokenizers")
        self.assertEqual(mount["mountPath"], "/remote-tokenizers")
        self.assertTrue(mount["readOnly"])
        config = yaml.safe_load(resource(rendered, "ConfigMap", "monitor-pii-engine-config-map")
                                ["data"]["ner.yaml"])
        self.assertNotIn("tokenizerClaimName", config)
        self.assertEqual(config["models"]["multilingual"]["revision"], "operator-verified-revision")

    def test_canonical_requires_future_compatible_image_and_rejects_legacy_conflicts(self):
        values = ner_values("disabled")
        del values["monitorPiiEngine"]["image"]
        with self.assertRaisesRegex(AssertionError, "0.13.0"):
            render("pii-engine", values)
        conflicts = [{"analyzerBackend": "remote-gliner"}, {"remote": {"models": [{"name": "old"}]}},
                     {"policy": {"pii": {"ner": {"strategy": "per-language"}}}},
                     {"device": "cuda", "accelerator": {"enabled": True}}]
        for conflict in conflicts:
            values = ner_values("disabled")
            values["monitorPiiEngine"].update(conflict)
            with self.subTest(conflict=conflict), self.assertRaises(AssertionError):
                render("pii-engine", values)

    def test_invalid_selection_and_remote_trust_fail_render(self):
        cases = [
            ("disabled", {"models": {"english": {"profile": "spacy-en-sm-v1"}}}),
            ("local", {"languageModels": {"fr": "english"}}),
            ("local", {"languageModels": {"en": "missing"}}),
            ("local", {"models": {"unused": {"profile": "spacy-en-sm-v1"}}}),
            ("local", {"capacity": {}}),
            ("local", {"unexpected": True}),
            ("local", {"models": {"English": {"profile": "spacy-en-sm-v1"}}}),
            ("local", {"models": {"english": {"profile": "spacy-en-sm-v1", "endpoint": "https://ner.example.com/extract"}}}),
            ("remote", {"egress": []}),
            ("remote", {"capacity": {"callTimeout": 0}}),
            ("remote", {"capacity": {"maxCalls": 10001}}),
            ("remote", {"capacity": {"maxResponseBytes": 8388609}}),
            ("remote", {"capacity": {"maxConcurrentCalls": 17}}),
        ]
        for mode, change in cases:
            values = ner_values(mode)
            values["monitorPiiEngine"]["ner"].update(change)
            with self.subTest(change=change), self.assertRaises(AssertionError):
                render("pii-engine", values)
        for cidr in ("0.0.0.0/0", "::/0", "999.2.3.4/32", "abcd/128", "1:::2/128",
                     ":1::2/128", "1:2:3:4:5:6:7:8::/128"):
            values = ner_values("remote")
            values["monitorPiiEngine"]["ner"]["egress"][0]["cidr"] = cidr
            with self.subTest(cidr=cidr), self.assertRaises(AssertionError):
                render("pii-engine", values)
        for change in ({"endpoint": "http://ner.example.com/extract"},
                       {"endpoint": "https://user:password@ner.example.com/extract"},
                       {"endpoint": "https://ner.example.com/extract?token=value"},
                       {"profile": "spacy-en-sm-v1"}, {"apiKey": "not-allowed"},
                       {"apiKeyFile": "/unapproved/path"}, {"apiKeySecretRef": {"name": "key"}}):
            values = ner_values("remote")
            values["monitorPiiEngine"]["ner"]["models"]["multilingual"].update(change)
            with self.subTest(change=change), self.assertRaises(AssertionError):
                render("pii-engine", values)
        values = ner_values("remote")
        del values["monitorPiiEngine"]["ner"]["models"]["multilingual"]["inferenceThreshold"]
        with self.assertRaisesRegex(AssertionError, "inferenceThreshold"):
            render("pii-engine", values)

    def test_long_ids_keep_volume_names_valid_and_ipv6_is_exact(self):
        values = ner_values("remote")
        ner = values["monitorPiiEngine"]["ner"]
        model_id = "m" * 64
        ner["models"] = {model_id: copy.deepcopy(ner["models"]["multilingual"])}
        ner["languageModels"] = {"en": model_id, "de": model_id}
        ner["egress"] = [{"cidr": "2001:db8::10/128", "port": 443}]
        rendered = render("pii-engine", values)
        pod = resource(rendered, "Deployment")["spec"]["template"]["spec"]
        self.assertTrue(all(len(item["name"]) <= 63 for item in pod["volumes"]))

    def test_null_preserves_legacy_and_inherited_defaults_are_safe(self):
        rendered = render("pii-engine", {"monitorPiiEngine": {"ner": None}})
        data = resource(rendered, "ConfigMap", "monitor-pii-engine-config-map")["data"]
        self.assertNotIn("ner.yaml", data)
        self.assertIn("ner", yaml.safe_load(data["policy.yaml"])["pii"])
        pod = resource(rendered, "Deployment")["spec"]["template"]["spec"]
        self.assertIn("model-cache", {item["name"] for item in pod["volumes"]})
