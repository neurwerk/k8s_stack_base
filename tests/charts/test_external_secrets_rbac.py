"""Token issuance is limited to the reader identities owned by each package."""

import subprocess
import unittest

import yaml

from helm import ROOT, documents, render


CONTROLLER = {
    "kind": "ServiceAccount",
    "name": "infra-external-secrets",
    "namespace": "infra-external-secrets",
}


def requests_tokens(rule):
    return (
        bool(set(rule.get("apiGroups", [])) & {"", "*"})
        and bool(set(rule.get("resources", [])) & {
            "serviceaccounts/token", "serviceaccounts/*", "*/token", "*",
        })
        and bool(set(rule.get("verbs", [])) & {"create", "*"})
    )


class ExternalSecretsRbacTests(unittest.TestCase):
    def assert_reader_grants(self, docs):
        expected = {
            (doc["metadata"]["namespace"],
             doc["spec"]["provider"]["vault"]["auth"]["kubernetes"]["serviceAccountRef"]["name"])
            for doc in docs if doc["kind"] == "SecretStore"
        }
        self.assertTrue(expected)
        roles = {
            (doc["kind"], doc["metadata"].get("namespace"), doc["metadata"]["name"]): doc
            for doc in docs if doc["kind"] in {"Role", "ClusterRole"}
        }
        granted = set()
        for binding in docs:
            if binding["kind"] not in {"RoleBinding", "ClusterRoleBinding"}:
                continue
            if CONTROLLER not in binding.get("subjects", []):
                continue
            namespace = binding["metadata"].get("namespace")
            reference = binding["roleRef"]
            role_namespace = namespace if reference["kind"] == "Role" else None
            role = roles[reference["kind"], role_namespace, reference["name"]]
            for rule in role["rules"]:
                if not requests_tokens(rule):
                    continue
                self.assertEqual(binding["kind"], "RoleBinding")
                self.assertEqual(reference["kind"], "Role")
                self.assertEqual(rule["apiGroups"], [""])
                self.assertEqual(rule["resources"], ["serviceaccounts/token"])
                self.assertEqual(rule["verbs"], ["create"])
                self.assertTrue(rule.get("resourceNames"))
                self.assertNotIn("*", rule["resourceNames"])
                granted.update((namespace, name) for name in rule["resourceNames"])
        self.assertEqual(granted, expected)

    def test_controller_default_has_no_token_creation_rule(self):
        docs = documents(render("external-secrets", namespace="infra-external-secrets"))
        bindings = [
            doc for doc in docs if doc["kind"] in {"RoleBinding", "ClusterRoleBinding"}
            and CONTROLLER in doc.get("subjects", [])
        ]
        self.assertTrue(bindings)
        for binding in bindings:
            reference = binding["roleRef"]
            role, = [
                doc for doc in docs if doc["kind"] == reference["kind"]
                and doc["metadata"]["name"] == reference["name"]
            ]
            self.assertFalse(any(requests_tokens(rule) for rule in role.get("rules", [])))

    def test_base_delivery_packages_grant_only_their_readers(self):
        packages = {
            path.parent for path in (ROOT / "releases").rglob("*.yaml")
            if any(
                isinstance(doc, dict) and doc.get("kind") == "SecretStore"
                for doc in yaml.safe_load_all(path.read_text())
            )
        }
        self.assertTrue(packages)
        for package in sorted(packages):
            with self.subTest(package=package.relative_to(ROOT)):
                result = subprocess.check_output(["kustomize", "build", str(package)], text=True)
                self.assert_reader_grants([doc for doc in yaml.safe_load_all(result) if doc])

    def test_shared_mcp_delivery_grants_only_its_reader_when_enabled(self):
        values = {"mcp-servers": {
            "enabled": True,
            "studioSetup": {"enabled": True},
            "catalog": {"presets": {"context7": {"enabled": True}}},
        }}
        self.assert_reader_grants(documents(render("agentgateway", values)))
