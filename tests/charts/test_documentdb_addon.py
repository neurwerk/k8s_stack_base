"""Shared DocumentDB provisioning is selected independently of the server."""

import subprocess
import unittest

from helm import render, resource


class DocumentDatabaseAddonTests(unittest.TestCase):
    def test_selected_provisioner_keeps_administrator_credentials_in_infrastructure(self):
        values = {"addon": {
            "enabled": True, "user": "synthetic_product", "database": "SyntheticProduct",
            "passwordSecret": "synthetic-documentdb-password",
        }}
        result = render("postgres/documentdb-addon", values, namespace="infra-postgres-operations")
        pod = resource(result, "Job")["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["initContainers"][0]["command"], ["stage-documentdb-provisioner"])
        container, = pod["containers"]
        env = {entry["name"]: entry for entry in container["env"]}
        self.assertEqual(env["DOCUMENTDB_USER"]["value"], "synthetic_product")
        self.assertEqual(env["DOCUMENTDB_DATABASE"]["value"], "SyntheticProduct")
        self.assertEqual(env["DOCUMENTDB_PASSWORD"]["valueFrom"]["secretKeyRef"], {
            "name": "synthetic-documentdb-password", "key": "password",
        })
        self.assertEqual(env["DOCUMENTDB_ADMIN_PASSWORD"]["valueFrom"]["secretKeyRef"], {
            "name": "postgres-operations-secret", "key": "admin-password",
        })
        for command in (
            ["kubeconform", "-strict", "-summary"],
            ["kube-linter", "lint", "-"],
        ):
            checked = subprocess.run(command, input=result.stdout, text=True, capture_output=True)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        for override in ({"user": "documentdb"}, {"postgresPort": 5433}, {"documentdbPort": 27017}):
            with self.subTest(override=override):
                failed = render("postgres/documentdb-addon", {"addon": {**values["addon"], **override}},
                                namespace="infra-postgres-operations", check=False)
                self.assertNotEqual(failed.returncode, 0)
        wrong_namespace = render("postgres/documentdb-addon", values, namespace="application", check=False)
        self.assertNotEqual(wrong_namespace.returncode, 0)

    def test_shared_server_needs_no_document_store_application_credentials(self):
        result = render("postgres/operations", {"postgresOperationsSecrets": {
            "adminPassword": "synthetic-admin", "agentgatewayPassword": "synthetic-gateway",
            "langfusePassword": "synthetic-traces",
        }, "postgresOperations": {"persistence": {"storageClassName": "synthetic-storage"}}},
            value_files=(), namespace="infra-postgres-operations")
        secret = resource(result, "Secret", "postgres-operations-secret")
        self.assertEqual(set(secret["stringData"]), {
            "admin-password", "agentgateway-password", "langfuse-password",
        })
        disabled = render("postgres/documentdb-addon", value_files=())
        self.assertNotIn("kind: Job", disabled.stdout)
