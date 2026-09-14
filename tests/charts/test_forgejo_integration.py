"""Optional package boundaries and shared-service Forgejo contracts."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_openrouter_catalog import ROOT, env_value, render, resources


class ForgejoIntegrationTests(unittest.TestCase):
    def test_database_opt_in_and_retention(self):
        disabled_result = render("postgres/operations", {"forgejo": {"enabled": False}})
        disabled = disabled_result.stdout
        self.assertNotIn("FORGEJO_PASSWORD", disabled)
        self.assertNotIn("forgejo-password:", disabled)
        missing = render("postgres/operations", {
            "forgejo": {"enabled": True},
            "postgresOperationsSecrets": {"forgejoPassword": ""},
        }, check=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("postgresOperationsSecrets.forgejoPassword is required", missing.stderr)
        enabled_result = render("postgres/operations", {
            "forgejo": {"enabled": True},
            "postgresOperationsSecrets": {"forgejoPassword": "fixture-only"},
        })
        enabled = enabled_result.stdout
        for kind in ("StatefulSet", "Secret"):
            disabled_resources = resources(disabled_result, kind)
            self.assertEqual(len(disabled_resources), 1)
            self.assertEqual(disabled_resources, resources(enabled_result, kind))
        self.assertRegex(resources(enabled_result, "Job")[0],
                         r"- name: FORGEJO_PASSWORD\n\s+valueFrom:\n\s+secretKeyRef:"
                         r"\n\s+name: forgejo-postgres-values\n\s+key: password\n")
        for contract in (
            "CREATE DATABASE forgejo OWNER forgejo",
            "REVOKE ALL ON DATABASE forgejo FROM PUBLIC",
            'verify_isolation forgejo "$FORGEJO_PASSWORD" postgres template1',
            'verify_isolation librechat "$DOCUMENTDB_PASSWORD" forgejo',
            'verify_isolation dify "$DIFY_PASSWORD" forgejo',
            'verify_isolation agentgateway "$AGENTGATEWAY_PASSWORD" forgejo',
            'verify_isolation langfuse "$LANGFUSE_PASSWORD" forgejo',
            'verify_isolation librechat_rag "$LIBRECHAT_RAG_PASSWORD" forgejo',
            "has_database_privilege('operations_admin', 'forgejo', 'CONNECT')",
            "kubernetes.io/metadata.name: forgejo",
        ):
            self.assertIn(contract, enabled)
        self.assertNotIn("DROP DATABASE", enabled + disabled)
        self.assertNotIn("DROP ROLE", enabled + disabled)

    def test_private_certificate_profiles_and_oidc(self):
        values = {"forgejo": {"enabled": True, "hostname": "forgejo.example.com"},
                  "externalGateway": {"enabled": False}}
        policies = render("cert-manager/approval-policy", values).stdout
        for issuer in ("staging", "production"):
            self.assertIn(f"name: cert-manager-forgejo-{issuer}", policies)
            self.assertIn(f"- cert-manager-forgejo-{issuer}", policies)
        self.assertIn('"forgejo.example.com"', policies)
        disabled = render("cert-manager/approval-policy", {"forgejo": {"enabled": False}}).stdout
        self.assertNotIn("cert-manager-forgejo", disabled)
        oidc = render("keycloak/oidc/forgejo", values)
        self.assertEqual(env_value(oidc, "KC_REDIRECT_URI"),
                         "https://forgejo.example.com/user/oauth2/keycloak/callback")
        self.assertEqual(env_value(oidc, "KC_DIRECT_ACCESS_GRANTS_ENABLED"), "false")
        self.assertEqual(env_value(oidc, "KC_SERVICE_ACCOUNTS_ENABLED"), "false")
        self.assertIn("key: oidcClientSecret", oidc.stdout)
        self.assertIn('"claim.name": "forgejo_roles"', oidc.stdout)
        self.assertIn('"implicitFlowEnabled": False', oidc.stdout)
        self.assertFalse(resources(oidc, "Secret"))
        self.assertFalse(render("keycloak/oidc/forgejo", {"forgejo": {"enabled": False}}).stdout.strip())

    def test_oidc_producer_roundtrip_and_consumers(self):
        # Use Helm's real Go/Sprig tpl, quote and YAML decoder, never a Python surrogate.
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            chart = Path(directory)
            (chart / "templates").mkdir()
            (chart / "Chart.yaml").write_text("apiVersion: v2\nname: quote-test\nversion: 0.1.0\n")
            for name, source in (("producer", "secret-sync"), ("consumer", "app")):
                (chart / f"{name}.yaml").write_text(
                    (ROOT / f"releases/forgejo/{source}/oidc.yaml").read_text())
            (chart / "templates/result.yaml").write_text('''
{{- $producer := .Files.Get "producer.yaml" | fromYaml -}}
{{- $consumer := .Files.Get "consumer.yaml" | fromYaml -}}
{{- $context := dict "oidcClientSecret" .Values.syntheticCredential "Template" .Template -}}
{{- $outputs := $producer.spec.target.template.data -}}
{{- $raw := tpl $outputs.oidcClientSecret $context -}}
{{- $decoded := tpl (index $outputs "values.yaml") $context | fromYaml -}}
{{- dict "apiVersion" "v1" "kind" "ConfigMap" "metadata" (dict "name" "quote-test")
    "data" (dict "raw" $raw "decoded" ($decoded | toJson)
    "producer" ($producer | toJson) "consumer" ($consumer | toJson)) | toJson -}}
''')
            modes = [{"forgejo": {"enabled": True, "hostname": "forgejo.example.com"},
                      "externalGateway": {"enabled": public}} for public in (False, True)]
            baselines = [render("keycloak/oidc/forgejo", mode).stdout for mode in modes]
            credentials = (
                "fixture-only", "fixture,externalGateway.enabled=true", 'fixture"quoted\'text',
                "fixture\nsecond-line\n", "fixture\\path\\end", "fixture{{ .other }}{braces}",
                "fixture-\u00e9-\u96ea-\u03bb", "", "fixture\r\n\tend",
                'fixture,forgejo.enabled=false"\\\n{{ .other }}\u96ea',
            )
            for credential in credentials:
                with self.subTest(credential=credential):
                    output = render(str(chart), {"syntheticCredential": credential}).stdout
                    data = json.loads(output[output.index("{"):])["data"]
                    decoded = json.loads(data["decoded"])
                    self.assertEqual(decoded, {"forgejoOidcClientSecret": credential})
                    self.assertEqual(data["raw"].encode("utf-8"), credential.encode("utf-8"))
                    for mode, baseline in zip(modes, baselines):
                        self.assertEqual(render("keycloak/oidc/forgejo", {**mode, **decoded}).stdout, baseline)
            producer = json.loads(data["producer"])
            self.assertEqual(producer, {
                "apiVersion": "external-secrets.io/v1", "kind": "ExternalSecret",
                "metadata": {"name": "forgejo-oidc-values", "namespace": "auth-keycloak"},
                "spec": {
                    "refreshInterval": "1h",
                    "secretStoreRef": {"name": "auth-keycloak-openbao-secret-store", "kind": "SecretStore"},
                    "target": {"name": "forgejo-oidc-values", "creationPolicy": "Owner", "deletionPolicy": "Retain",
                        "template": {"engineVersion": "v2", "type": "Opaque", "mergePolicy": "Replace",
                            "metadata": {"labels": {"reconcile.fluxcd.io.watch": "Enabled"}},
                            "data": {"oidcClientSecret": "{{ .oidcClientSecret }}",
                                     "values.yaml": "forgejoOidcClientSecret: {{ .oidcClientSecret | quote }}\n"}}},
                    "data": [{"secretKey": "oidcClientSecret", "remoteRef": {
                        "key": "auth-keycloak/internal", "property": "forgejoClientSecret"}}],
                },
            })
            consumer = json.loads(data["consumer"])
            self.assertEqual(consumer["metadata"], {"name": "keycloak-forgejo-oidc", "namespace": "auth-keycloak"})
            self.assertEqual(consumer["spec"]["valuesFrom"], [
                {"kind": "Secret", "name": "forgejo-oidc-values", "valuesKey": "values.yaml"},
                {"kind": "ConfigMap", "name": "client-values", "valuesKey": "values.yaml"},
                {"kind": "ConfigMap", "name": "keycloak-product-values", "valuesKey": "values.yaml"},
            ])
            for baseline in baselines:
                self.assertRegex(baseline, r"- name: KC_CLIENT_SECRET\n\s+valueFrom:\n\s+secretKeyRef:"
                                 r"\n\s+name: forgejo-oidc-values\n\s+key: oidcClientSecret\n")
                self.assertNotIn("forgejoOidcClientSecret", baseline)
                self.assertNotIn("kind: Secret\n", baseline)
            self.assertFalse(render("keycloak/oidc/forgejo", {
                "forgejo": {"enabled": False}, "forgejoOidcClientSecret": credentials[-1],
            }).stdout.strip())

    def test_optional_inventory_does_not_leak_into_default_stages(self):
        for stage in ("namespaces", "infrastructure", "applications"):
            result = subprocess.run([
                "kustomize", "build", "--load-restrictor", "LoadRestrictionsNone",
                str(ROOT / "releases" / stage),
            ], capture_output=True, text=True, check=True)
            self.assertNotIn("name: keycloak-forgejo-oidc", result.stdout)
            self.assertNotIn("name: forgejo-runtime", result.stdout)
            self.assertNotIn("name: forgejo\n", result.stdout)
        for stage in ("namespaces/forgejo", "forgejo/secret-sync", "forgejo/app"):
            result = subprocess.run([
                "kustomize", "build", "--load-restrictor", "LoadRestrictionsNone",
                str(ROOT / "releases" / stage),
            ], capture_output=True, text=True, check=True)
            if stage == "forgejo/secret-sync":
                postgres = [document for document in resources(result, "ExternalSecret")
                            if "  name: forgejo-postgres-values\n" in document]
                self.assertEqual(len(postgres), 1)
                self.assertIn("        password: '{{ .dbPassword }}'\n", postgres[0])
                self.assertIn("            forgejoPassword: {{ .dbPassword | quote }}\n",
                              postgres[0])
