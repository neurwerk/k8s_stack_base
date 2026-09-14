"""Synthetic, offline composition tests; no client fixtures or Secret resolution."""

from __future__ import annotations

import contextlib
import copy
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from scripts import access_composition as composition
from scripts import access_git
from scripts import check_client_application_access as adapter

ROOT = Path(__file__).resolve().parents[2]


def resource(kind, name, spec=None, namespace="flux-system"):
    api = {"Kustomization": "kustomize.toolkit.fluxcd.io/v1", "HelmRelease": "helm.toolkit.fluxcd.io/v2",
           "GitRepository": "source.toolkit.fluxcd.io/v1", "ExternalSecret": "external-secrets.io/v1"}.get(kind, "v1")
    return {"apiVersion": api, "kind": kind, "metadata": {"name": name, "namespace": namespace},
            **({"spec": spec} if spec is not None else {})}


def kustomization(resources=(), **kwargs):
    return {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
            "resources": list(resources), **kwargs}


def values():
    result = {
        "publicCertificates": {"useProduction": True},
        "canonicalEndpointRouting": {"mode": "internal-traefik"},
        "externalGateway": {"enabled": False, "port": 443},
        "authKeycloak": {"hostname": "login.example.com", "realm": "example"},
        "frontendLibrechat": {"hostname": "chat.example.com", "objectStorage": {
            "enabled": True, "endpoint": "", "forcePathStyle": True},
            "adminPanel": {"hostname": "admin.example.com", "url": ""}},
        "infraRookCeph": {"objectStore": {"publicHostname": "files.example.com", "externalGateway": {"enabled": True, "port": 443}}},
        "frontendStudio": {"studio": {"hostname": "studio.example.com", "oidcAuthority": "https://login.example.com/realms/example"}},
        "frontendDify": {"hostname": "dify.example.com", "config": {"ENABLE_SOCIAL_OAUTH_LOGIN": "true"}},
        "monitorLangfuseWrapper": {"hostname": "traces.example.com"},
        "langfuse": {"langfuse": {"nextauth": {"url": "https://traces.example.com"}, "ingress": {"enabled": False}}},
        "infraAgentgatewayWrapper": {"hostname": "gateway.example.com"},
        "forgejo": {"enabled": True, "hostname": "git.example.com"},
    }
    for name, host in (("librechat", "chat"), ("studio", "studio"), ("dify", "dify"), ("agentgateway", "gateway")):
        path = {"librechat": "/oauth/openid/callback", "studio": "/auth/callback", "dify": "/console/api/oauth/authorize/keycloak"}.get(name)
        result["authKeycloak"][name + "RedirectUri"] = f"https://{host}.example.com{path}" if path else ""
        result["authKeycloak"][name + "WebOrigin"] = f"https://{host}.example.com" if path else ""
    result["authKeycloak"].update(librechatAdminRedirectUri="https://chat.example.com/api/admin/oauth/openid/callback",
                                  librechatAdminWebOrigin="https://admin.example.com")
    return result


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client, self.platform = self.root / "client", self.root / "platform"
        self.cluster = self.client / "clusters/prod-eu-1"
        self.policy = {"access": {"boundary": "internet", "default": "internal"}, "endpoints": {}}
        self.inventory = []
        self.config = values()
        self.write(self.client / "config/application-access.yaml", self.policy)
        self.write(self.client / "config/client.yaml", self.config)
        self.write(self.cluster / "kustomization.yaml", kustomization(["selection.yaml", "flux-system"]))
        self.write(self.cluster / "selection.yaml", resource("Kustomization", "applications", {
            "path": "./releases/apps", "sourceRef": {"kind": "GitRepository", "name": "k8s-stack"}}))
        self.write(self.cluster / "flux-system/kustomization.yaml", kustomization(["sync.yaml"]))
        self.write(self.cluster / "flux-system/sync.yaml", [resource("GitRepository", "k8s-stack", {"url": adapter.BASE_URL, "ref": {"branch": "main"}}),
            resource("Kustomization", "flux-system", {"path": "./clusters/prod-eu-1", "sourceRef": {"kind": "GitRepository", "name": "flux-system"}})])
        self.write(self.platform / "releases/apps/kustomization.yaml", kustomization())
        self.mock_revisions = patch.object(adapter, "revisions", return_value={"platform": "a" * 40, "checker": "b" * 40})
        self.mock_revisions.start()
        self.addCleanup(self.mock_revisions.stop)
        self.mock_snapshot = patch.object(adapter, "GitSnapshot", return_value=SimpleNamespace(
            read=lambda path: path.read_bytes(), require=lambda path: None))
        self.snapshot_factory = self.mock_snapshot.start()
        self.addCleanup(self.mock_snapshot.stop)

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump_all(data) if isinstance(data, list) else yaml.safe_dump(data), encoding="utf-8")

    def chart(self, name, overrides=None, refs=(), namespace="apps"):
        self.write(self.platform / f"charts/{name}/values.yaml", self.config)
        if name in composition.VENDOR_DEFAULTS:
            self.write(self.platform / f"charts/{name}/Chart.yaml", {"dependencies": [{"name": name, "version": "1.0.0"}]})
            archive = self.platform / f"charts/{name}/charts/{name}-1.0.0.tgz"
            archive.parent.mkdir(parents=True, exist_ok=True)
            defaults = {"langfuse": {"ingress": {"enabled": False}}} if name == "langfuse" else {
                component: {"ingress": {"enabled": False}} for component in ("grafana", "prometheus", "alertmanager")}
            payload = yaml.safe_dump(defaults).encode()
            with tarfile.open(archive, "w:gz") as package:
                info = tarfile.TarInfo(name + "/values.yaml")
                info.size = len(payload)
                package.addfile(info, io.BytesIO(payload))
        release = resource("HelmRelease", name.replace("/", "-"), {"chart": {"spec": {
            "chart": f"./charts/{name}", "sourceRef": {"kind": "GitRepository", "name": "k8s-stack", "namespace": "flux-system"}}},
            "valuesFrom": list(refs), "values": overrides or {}}, namespace)
        filename = name.replace("/", "-") + ".yaml"
        self.write(self.platform / "releases/apps" / filename, release)
        self.inventory.append(filename)
        self.write(self.platform / "releases/apps/kustomization.yaml", kustomization(self.inventory))
        return release, self.platform / "releases/apps" / filename

    def select(self, *names):
        for name in names:
            self.policy["endpoints"][name] = {}
            for chart in adapter.BUNDLES[name][1]:
                self.chart(chart)
            if name in adapter.CALLBACKS or name == "forgejo":
                self.chart("keycloak/oidc/" + name)
            if name == "librechat":
                self.policy["endpoints"]["librechat-files"] = {}
        self.write(self.client / "config/application-access.yaml", self.policy)

    def derive(self):
        return adapter.derive_plan(self.client, self.platform)[0]

    def test_full_catalog_and_read_only_no_commands(self):
        self.select(*adapter.BUNDLES)
        self.chart("rook-ceph")
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch.object(access_git.subprocess, "run", side_effect=AssertionError("no commands")):
            plan = self.derive()
        self.assertEqual(set(plan["endpoints"]), set(adapter.BROWSER_DEPENDENCIES))
        self.assertEqual(plan["endpoints"]["librechat"]["features"], {"files": True})
        self.assertEqual(plan["endpoints"]["dify"]["features"], {"consoleSSO": True})
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_actual_platform_releases_with_synthetic_client_generators(self):
        self.platform = ROOT
        self.snapshot_factory.return_value = access_git.GitSnapshot(ROOT)
        self.config["externalGateway"]["enabled"] = True
        for field in adapter.GATEWAY_PORTS.values():
            node = self.config
            parts = field.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = 443
        self.write(self.client / "config/client.yaml", self.config)
        stages = [resource("Kustomization", name, {"path": path, "sourceRef": {
            "kind": "GitRepository", "name": "k8s-stack"}}) for name, path in (
                ("applications", "./releases/applications"), ("infrastructure", "./releases/infrastructure"))]
        self.write(self.cluster / "selection.yaml", stages)
        comp = composition.Composition(self.client, ROOT, "prod-eu-1")
        needed = set()
        for obj in comp.objects.values():
            if obj["kind"] == "HelmRelease":
                namespace = obj["metadata"]["namespace"]
                for ref in obj["spec"].get("valuesFrom", []):
                    if ref["kind"] == "ConfigMap" and ("", "ConfigMap", namespace, ref["name"]) not in comp.objects:
                        needed.add((namespace, ref["name"]))
        generators = [{"name": name, "namespace": namespace, "files": ["values.yaml=../config/client.yaml"]}
                      for namespace, name in sorted(needed)]
        self.write(self.client / "apps/kustomization.yaml", kustomization(
            generatorOptions={"disableNameSuffixHash": True}, configMapGenerator=generators))
        stages.append(resource("Kustomization", "client-values", {"path": "./apps", "sourceRef": {
            "kind": "GitRepository", "name": "flux-system"}}))
        self.write(self.cluster / "selection.yaml", stages)
        self.policy["endpoints"] = {name: {} for name in adapter.BROWSER_DEPENDENCIES if name != "forgejo"}
        self.write(self.client / "config/application-access.yaml", self.policy)
        with patch.object(access_git.subprocess, "run", side_effect=AssertionError("no commands")), patch("socket.socket", side_effect=AssertionError("no network")):
            plan = self.derive()
        self.assertEqual(set(plan["endpoints"]), set(adapter.BROWSER_DEPENDENCIES) - {"forgejo"})
        del self.config["authKeycloak"]["keycloak"]
        self.write(self.client / "config/client.yaml", self.config)
        self.assertEqual(self.derive(), plan)
        with patch.object(composition.Composition, "secret_shape", return_value=composition.UNKNOWN):
            with self.assertRaisesRegex(adapter.PlanError, "unresolved"):
                self.derive()

    def test_selected_producer_scope_and_scalar_precedence(self):
        self.select("keycloak")
        path = self.platform / "releases/apps/keycloak-server.yaml"
        release = composition.document(composition.read(path))
        release["spec"]["valuesFrom"] = [{"kind": "Secret", "name": "opaque"}]
        self.write(path, release)
        producer = resource("ExternalSecret", "producer", {"target": {"name": "opaque", "creationPolicy": "Owner",
            "template": {"engineVersion": "v2", "data": {
                "values.yaml": 'privateConfig:\n  password: {{ .password | quote }}\n  literal: false\n',
                "authKeycloak": "{{ .password }}"}}}}, "apps")
        # Merely present producers, and upstream references, are never resolved.
        self.write(self.platform / "releases/apps/producer.yaml", producer)
        with self.assertRaisesRegex(adapter.PlanError, "unresolved authKeycloak.hostname"):
            self.derive()
        self.inventory.append("producer.yaml")
        self.write(self.platform / "releases/apps/kustomization.yaml", kustomization(self.inventory))
        with patch.object(access_git.subprocess, "run", side_effect=AssertionError("no commands")), patch("socket.socket", side_effect=AssertionError("no network")):
            self.assertEqual(set(self.derive()["endpoints"]), {"keycloak"})
        comp = composition.Composition(self.client, self.platform, "prod-eu-1")
        shape = comp.secret_shape("apps", "opaque", "values.yaml")
        self.assertEqual(set(shape), {"privateConfig"})
        self.assertIs(shape["privateConfig"]["password"], composition.UNKNOWN)
        self.assertIs(shape["privateConfig"]["literal"], composition.UNKNOWN)
        producer["spec"]["target"]["template"]["data"]["values.yaml"] = 'authKeycloak:\n  hostname: {{ .hostname | quote }}\n'
        self.write(self.platform / "releases/apps/producer.yaml", producer)
        with self.assertRaisesRegex(adapter.PlanError, "unresolved authKeycloak.hostname"):
            self.derive()
        release["spec"]["values"] = {"authKeycloak": {"realm": "example"}}
        self.write(path, release)
        with self.assertRaisesRegex(adapter.PlanError, "unresolved authKeycloak.hostname"):
            self.derive()
        release["spec"]["values"]["authKeycloak"]["hostname"] = "login.example.com"
        self.write(path, release)
        self.derive()

    def test_apply_suppression_cannot_supply_configmap_or_producer_facts(self):
        self.select("keycloak")
        release_path = self.platform / "releases/apps/keycloak-server.yaml"
        release = composition.document(composition.read(release_path))
        root_path = self.platform / "releases/apps/kustomization.yaml"
        ssa, reconcile = "kustomize.toolkit.fluxcd.io/ssa", "kustomize.toolkit.fluxcd.io/reconcile"
        for kind in ("ConfigMap", "ExternalSecret"):
            facts = resource(kind, "facts", namespace="apps")
            if kind == "ConfigMap":
                facts["data"] = {"values.yaml": yaml.safe_dump(self.config)}
            else:
                facts["spec"] = {"target": {"name": "facts", "creationPolicy": "Owner", "template": {
                    "engineVersion": "v2", "data": {"values.yaml": 'privateConfig:\n  password: {{ .password | quote }}\n'}}}}
            release["spec"]["valuesFrom"] = [{"kind": "ConfigMap" if kind == "ConfigMap" else "Secret", "name": "facts"}]
            self.write(release_path, release)
            for placement in ("resource", "common"):
                for annotations, valid in (({}, True), ({ssa: "Override", reconcile: "enabled"}, True),
                                           ({ssa: "Ignore"}, False), ({ssa: "IfNotPresent"}, False),
                                           ({ssa: "Merge"}, False), ({reconcile: "disabled"}, False)):
                    obj = copy.deepcopy(facts)
                    config = kustomization([*self.inventory, "facts.yaml"])
                    if placement == "resource":
                        obj["metadata"]["annotations"] = annotations
                    else:
                        config["commonAnnotations"] = annotations
                    self.write(self.platform / "releases/apps/facts.yaml", obj)
                    self.write(root_path, config)
                    with self.subTest(kind=kind, placement=placement, annotations=annotations):
                        if valid:
                            self.derive()
                        else:
                            with self.assertRaisesRegex(adapter.PlanError, "management annotations unsupported"):
                                self.derive()
        release["spec"]["valuesFrom"] = [{"kind": "ConfigMap", "name": "facts"}]
        self.write(release_path, release)
        self.write(root_path, kustomization(self.inventory, namespace="apps",
            generatorOptions={"disableNameSuffixHash": True, "annotations": {ssa: "IfNotPresent"}},
            configMapGenerator=[{"name": "facts", "files": ["values.yaml=../../charts/keycloak/server/values.yaml"]}]))
        with self.assertRaisesRegex(adapter.PlanError, "management annotations unsupported"):
            self.derive()

    def test_unsupported_or_ambiguous_producer_stays_wholly_unknown(self):
        comp = composition.Composition(self.client, self.platform, "prod-eu-1")
        template = {"engineVersion": "v2", "data": {
            "values.yaml": 'privateConfig:\n  password: {{ .password | quote }}\n', "password": "{{ .password }}"}}
        producer = resource("ExternalSecret", "producer", {"target": {
            "name": "opaque", "creationPolicy": "Owner", "template": template}}, "apps")
        cases = []
        for target_field, value in (("creationPolicy", "Merge"), ("creationPolicy", "Orphan"), ("name", "different")):
            obj = copy.deepcopy(producer)
            obj["spec"]["target"][target_field] = value
            cases.append([obj])
        for field, value in (("mergePolicy", "Merge"), ("templateFrom", []), ("engineVersion", "v1"),
                             ("metadata", {"name": "different"}), ("metadata", {"labels": {"dynamic": "{{ .label }}"}}),
                             ("data", {"different.yaml": "a: b"}), ("data", {"values.yaml": "a: b", "extra": "mixed"}),
                             ("data", {"{{ .key }}": "a: b"})):
            obj = copy.deepcopy(producer)
            obj["spec"]["target"]["template"][field] = value
            cases.append([obj])
        for key, value in (
                ("{{ .key }}", "{{ .password }}"), ("..reserved", "{{ .password }}"),
                ("raw", "prefix{{ .password }}")):
            obj = copy.deepcopy(producer)
            obj["spec"]["target"]["template"]["data"][key] = value
            cases.append([obj])
        for raw in ('privateConfig: {{ .password }}', 'privateConfig: {{ .password | toYaml }}',
                    'privateConfig: "prefix {{ .password | quote }}"', '{{ .key | quote }}: value',
                    'privateConfig: {{ .password | quote }}suffix', 'privateConfig: {{ .password | quote }}: value',
                    'privateConfig: &value {}\nother: *value', 'privateConfig: {<<: {password: value}}',
                    'privateConfig: !!str value', 'privateConfig: |\n  password: {{ .password | quote }}\n',
                    'privateConfig: "{{ .password | quote }}"', 'privateConfig: [{password: value}]',
                    'privateConfig: {{ .password | quote }}\nprivateConfig: {}', 'privateConfig: {"{{ .key }}": value}'):
            obj = copy.deepcopy(producer)
            obj["spec"]["target"]["template"]["data"]["values.yaml"] = raw
            cases.append([obj])
        wrong_namespace = copy.deepcopy(producer)
        wrong_namespace["metadata"]["namespace"] = "other"
        second = copy.deepcopy(producer)
        second["metadata"]["name"] = "second-producer"
        cases.extend([[], [wrong_namespace], [producer, second]])
        for index, objects in enumerate(cases):
            comp.objects = {index: obj for index, obj in enumerate(objects)}
            with self.subTest(case=index):
                self.assertIs(comp.secret_shape("apps", "opaque", "values.yaml"), composition.UNKNOWN)
        template["mergePolicy"] = "Replace"
        comp.objects = {0: producer}
        self.assertIs(comp.secret_shape("apps", "opaque", "values.yaml")["privateConfig"]["password"], composition.UNKNOWN)
        self.assertIs(comp.secret_shape("apps", "opaque", "missing-key"), composition.UNKNOWN)

    def test_selected_not_present_and_forgejo_separate_stage(self):
        self.select("keycloak")
        self.write(self.platform / "charts/forgejo/values.yaml", self.config)
        self.assertEqual(set(self.derive()["endpoints"]), {"keycloak"})
        self.select("forgejo")
        app_resources = self.inventory[:-2]
        self.write(self.platform / "releases/apps/kustomization.yaml", kustomization(app_resources))
        self.write(self.platform / "releases/forgejo/kustomization.yaml", kustomization(["../apps/forgejo.yaml", "../apps/keycloak-oidc-forgejo.yaml"]))
        original = composition.document(composition.read(self.cluster / "selection.yaml"))
        self.write(self.cluster / "selection.yaml", [original, resource("Kustomization", "forgejo", {
            "path": "./releases/forgejo", "sourceRef": {"kind": "GitRepository", "name": "k8s-stack"}})])
        self.assertIn("forgejo", self.derive()["endpoints"])
        for suspend in (True, "false"):
            original["spec"]["suspend"] = suspend
            self.write(self.cluster / "selection.yaml", original)
            with self.assertRaisesRegex(adapter.PlanError, "suspended"):
                self.derive()

    def test_schema_inventory_and_yaml_safety(self):
        self.select("keycloak")
        for field in ("features", "hostnames", "certificates", "canonicalEndpointRouting", "enabled"):
            policy = copy.deepcopy(self.policy)
            policy["endpoints"]["keycloak"][field] = {}
            self.write(self.client / "config/application-access.yaml", policy)
            with self.subTest(field=field), self.assertRaises(adapter.PlanError):
                self.derive()
        self.policy["endpoints"]["forgejo"] = {}
        self.write(self.client / "config/application-access.yaml", self.policy)
        with self.assertRaisesRegex(adapter.PlanError, "not selected: forgejo"):
            self.derive()
        for raw in ("a: 1\na: 2", "a: &x {}\nb: *x", "a: !!str x", "a: {<<: {b: 1}}", "? [a]\n: b", "[" * 1500):
            with self.subTest(raw=raw[:20]), self.assertRaises(adapter.PlanError):
                composition.document(raw)

    def test_paths_transforms_unknown_chart_and_cycles(self):
        self.select("keycloak")
        for path in ("https://example.com/x", "/outside", "../../outside", "git::example.com", "missing", "bad\x00path"):
            with self.subTest(path=path), self.assertRaises(adapter.PlanError):
                composition.local(self.client, self.client, path)
        (self.client / "escape").symlink_to(self.platform, target_is_directory=True)
        with self.assertRaisesRegex(adapter.PlanError, "escapes"):
            composition.local(self.client, self.client, "escape")
        for field in ("patches", "images", "components", "replacements", "secretGenerator", "transformers", "helmCharts"):
            self.write(self.platform / "releases/apps/kustomization.yaml", kustomization(self.inventory, **{field: []}))
            with self.subTest(field=field), self.assertRaises(adapter.PlanError):
                self.derive()
        self.write(self.platform / "releases/apps/kustomization.yaml", kustomization(["."]))
        with self.assertRaisesRegex(adapter.PlanError, "cycle"):
            self.derive()
        self.chart("unknown-serving-chart")
        with self.assertRaisesRegex(adapter.PlanError, "unknown selected chart"):
            self.derive()

    def test_ordered_cm_secret_inline_and_namespace_identity(self):
        self.select("keycloak")
        cm = resource("ConfigMap", "facts", namespace="apps")
        cm["data"] = {"values.yaml": yaml.safe_dump({"authKeycloak": {"hostname": "login.example.com"}})}
        self.write(self.platform / "releases/apps/facts.yaml", cm)
        self.inventory.append("facts.yaml")
        refs = [{"kind": "ConfigMap", "name": "facts"}, {"kind": "Secret", "name": "opaque"}]
        release, path = self.chart("keycloak/server", self.config, refs)
        self.inventory.remove("keycloak-server.yaml")
        self.write(self.platform / "releases/apps/kustomization.yaml", kustomization(self.inventory))
        self.assertIn("keycloak", self.derive()["endpoints"])
        release["spec"]["values"] = {"authKeycloak": {"realm": "example"}}
        self.write(path, release)
        with self.assertRaisesRegex(adapter.PlanError, "unresolved authKeycloak.hostname"):
            self.derive()
        release["spec"]["valuesFrom"].reverse()
        release["spec"]["values"] = {"publicCertificates": {"useProduction": True}, "canonicalEndpointRouting": {"mode": "internal-traefik"}, "externalGateway": {"enabled": False}}
        self.write(path, release)
        self.assertIn("keycloak", self.derive()["endpoints"])
        cm["metadata"]["namespace"] = "another"
        self.write(self.platform / "releases/apps/facts.yaml", cm)
        with self.assertRaisesRegex(adapter.PlanError, "namespace-local ConfigMap missing"):
            self.derive()

    def test_projection_table(self):
        unknown = composition.UNKNOWN
        cases = [([{"a": {"b": True}}, unknown, {"a": {"c": False}}], None),
                 ([unknown, {"a": {"b": False}}], False),
                 ([{"a": {"b": True}}, {"other": unknown}], True),
                 ([unknown, {"a": {}}], None), ([{"a": None}], None),
                 ([{"a": []}], None), ([{"a": {"b": "false"}}], None)]
        for layers, expected in cases:
            with self.subTest(expected=expected):
                if expected is None:
                    with self.assertRaises(adapter.PlanError):
                        composition.leaf(layers, "a.b", bool, "dify")
                else:
                    self.assertIs(composition.leaf(layers, "a.b", bool, "dify"), expected)

    def test_optional_unknown_target_path_and_no_secret_file_read(self):
        self.select("keycloak")
        release = composition.document(composition.read(self.platform / "releases/apps/keycloak-server.yaml"))
        self.write(self.platform / "opaque.yaml", {"kind": "Secret", "doNotRead": True})
        refs = [{"kind": "ConfigMap", "name": "missing", "optional": True}]
        release["spec"]["valuesFrom"] = refs
        self.write(self.platform / "releases/apps/keycloak-server.yaml", release)
        with self.assertRaisesRegex(adapter.PlanError, "unresolved"):
            self.derive()
        for target in ("private.password", "authKeycloak", "a[0]", "a\\.b", None, ""):
            release["spec"]["valuesFrom"] = [{"kind": "Secret", "name": "opaque", "targetPath": target}]
            self.write(self.platform / "releases/apps/keycloak-server.yaml", release)
            with self.subTest(target=target):
                with self.assertRaises(adapter.PlanError):
                    self.derive()
        release["spec"]["valuesFrom"] = [{"kind": "Secret", "name": "opaque", "targetPath": "authKeycloak.hostname"}]
        release["spec"]["values"] = self.config
        self.write(self.platform / "releases/apps/keycloak-server.yaml", release)
        with self.assertRaisesRegex(adapter.PlanError, "opaque Secret targetPath"):
            self.derive()

    def test_unsupported_reconciliation_and_duplicate_resources(self):
        self.select("keycloak")
        path = self.platform / "releases/apps/keycloak-server.yaml"
        original = composition.document(composition.read(path))
        for field, value in (("postRenderers", []), ("chartRef", {}), ("suspend", True),
                             ("upgrade", {"preserveValues": True}), ("install", {"disableHooks": True})):
            obj = copy.deepcopy(original)
            obj["spec"][field] = value
            self.write(path, obj)
            with self.subTest(field=field), self.assertRaises(adapter.PlanError):
                self.derive()
        self.write(path, original)
        self.write(self.platform / "releases/apps/kustomization.yaml", kustomization(self.inventory * 2))
        with self.assertRaisesRegex(adapter.PlanError, "duplicate resource identity"):
            self.derive()
        self.write(self.platform / "releases/apps/kustomization.yaml", kustomization(self.inventory))
        selection = composition.document(composition.read(self.cluster / "selection.yaml"))
        for field in ("postBuild", "decryption", "patches", "targetNamespace"):
            obj = copy.deepcopy(selection)
            obj["spec"][field] = {}
            self.write(self.cluster / "selection.yaml", obj)
            with self.subTest(field=field), self.assertRaises(adapter.PlanError):
                self.derive()
        self.write(self.cluster / "selection.yaml", selection)
        self.write(self.client / ".sourceignore", "config")
        with self.assertRaisesRegex(adapter.PlanError, "source ignore"):
            self.derive()

    def test_namespace_generators_and_untrusted_bootstrap_marker(self):
        self.select("keycloak")
        self.write(self.platform / "releases/apps/facts.yaml", resource("ConfigMap", "facts", namespace="other"))
        self.write(self.platform / "releases/apps/kustomization.yaml", kustomization(
            [*self.inventory, "facts.yaml"], namespace="apps", generatorOptions={"disableNameSuffixHash": True},
            configMapGenerator=[{"name": "facts", "files": ["values.yaml=../../charts/keycloak/server/values.yaml"]}]))
        with self.assertRaisesRegex(adapter.PlanError, "duplicate resource identity"):
            self.derive()
        obj = resource("Deployment", "not-a-controller", namespace="apps")
        obj["apiVersion"], obj["_bootstrap"] = "apps/v1", True
        self.write(self.platform / "releases/apps/facts.yaml", obj)
        self.write(self.platform / "releases/apps/kustomization.yaml", kustomization([*self.inventory, "facts.yaml"]))
        with self.assertRaisesRegex(adapter.PlanError, "unsupported selected resource"):
            self.derive()

    def test_dify_string_false_and_routing_certificate_gates(self):
        self.config["frontendDify"]["config"]["ENABLE_SOCIAL_OAUTH_LOGIN"] = "false"
        self.select("dify")
        self.assertEqual(self.derive()["endpoints"]["dify"]["features"], {"consoleSSO": False})
        path = self.platform / "releases/apps/dify-web.yaml"
        original = composition.document(composition.read(path))
        for override in ({"publicCertificates": {"useProduction": False}},
                         {"externalGateway": {"enabled": True}, "frontendDify": {"webGui": {"ports": {"gateway": {"tls": 8443}}}}}):
            changed = copy.deepcopy(original)
            changed["spec"]["values"] = override
            self.write(path, changed)
            with self.subTest(override=override), self.assertRaises(adapter.PlanError):
                self.derive()
        self.write(path, original)
        path = self.platform / "releases/apps/dify-api.yaml"
        changed = composition.document(composition.read(path))
        changed["spec"]["values"] = {"canonicalEndpointRouting": {"mode": None}}
        self.write(path, changed)
        with self.assertRaisesRegex(adapter.PlanError, "unsupported scalar"):
            self.derive()

    def test_native_agentgateway_rejects_browser_callback_pair(self):
        self.select("keycloak", "agentgateway")
        self.policy["endpoints"]["agentgateway"] = {"level": "public"}
        self.write(self.client / "config/application-access.yaml", self.policy)
        self.assertEqual(set(self.derive()["endpoints"]), {"keycloak", "agentgateway"})
        path = self.platform / "releases/apps/keycloak-oidc-agentgateway.yaml"
        release = composition.document(composition.read(path))
        for redirect, web_origin in (
                ("https://gateway.example.com/callback", "https://gateway.example.com"),
                ("", "https://gateway.example.com"),
                ("https://gateway.example.com/callback", ""),
                ("https://other.example.com/callback", "https://gateway.example.com"),
                ("https://gateway.example.com/callback", "https://gateway.example.com/extra"),
                ("http://gateway.example.com/callback", "https://gateway.example.com")):
            release["spec"]["values"] = {"authKeycloak": {"agentgatewayRedirectUri": redirect, "agentgatewayWebOrigin": web_origin}}
            self.write(path, release)
            with self.subTest(redirect=redirect, web_origin=web_origin):
                with self.assertRaisesRegex(adapter.PlanError, "browser callback mode unsupported"):
                    self.derive()

    def test_exact_browser_callback_and_web_origin_paths(self):
        self.select("keycloak", "librechat", "librechat-admin", "studio", "dify")
        for name, prefix in (("librechat", "librechat"), ("librechat", "librechatAdmin"), ("studio", "studio"), ("dify", "dify")):
            path = self.platform / "releases/apps" / ("keycloak-oidc-" + name + ".yaml")
            release = composition.document(composition.read(path))
            for suffix, ending in (("RedirectUri", "/wrong"), ("RedirectUri", "/*"), ("WebOrigin", "/extra")):
                key = prefix + suffix
                canonical = self.config["authKeycloak"][prefix + "WebOrigin"]
                if prefix == "librechatAdmin" and suffix == "RedirectUri":
                    canonical = "https://chat.example.com"
                changed = copy.deepcopy(release)
                changed["spec"]["values"] = {"authKeycloak": {key: canonical + ending}}
                self.write(path, changed)
                with self.subTest(name=name, key=key, ending=ending), self.assertRaisesRegex(adapter.PlanError, "path disagrees"):
                    self.derive()
            self.write(path, release)

    def test_origins_storage_features_and_unknowns(self):
        self.select(*adapter.BUNDLES)
        self.chart("rook-ceph")
        cases = [
            ("librechat/shared", {"frontendLibrechat": {"objectStorage": {"enabled": False}}}, "file feature mismatch"),
            ("librechat/app", {"frontendLibrechat": {"objectStorage": {"endpoint": "https://other.example.com"}}}, "unclassified"),
            ("librechat/app", {"frontendLibrechat": {"objectStorage": {"forcePathStyle": False}}}, "virtual-hosted"),
            ("rook-ceph", {"infraRookCeph": {"objectStore": {"publicHostname": "other.example.com"}}}, "Rook route disagree"),
            ("studio/web", {"frontendStudio": {"studio": {"oidcAuthority": "https://other.example.com/realms/example"}}}, "OIDC authority"),
            ("studio/api", {"frontendStudio": {"studio": {"hostname": "other.example.com"}}}, "bundle hostname mismatch"),
            ("dify/api", {"frontendDify": {"config": {"ENABLE_SOCIAL_OAUTH_LOGIN": False}}}, "unsupported scalar"),
            ("langfuse", {"langfuse": {"langfuse": {"nextauth": {"url": "https://other.example.com"}}}}, "advertised origin"),
            ("keycloak/oidc/librechat", {"authKeycloak": {"librechatAdminRedirectUri": "https://admin.example.com/callback"}}, "advertised origin"),
        ]
        for chart, overrides, error in cases:
            path = self.platform / "releases/apps" / (chart.replace("/", "-") + ".yaml")
            original = composition.document(composition.read(path))
            changed = copy.deepcopy(original)
            changed["spec"]["values"] = overrides
            self.write(path, changed)
            with self.subTest(chart=chart, error=error), self.assertRaisesRegex(adapter.PlanError, error):
                self.derive()
            self.write(path, original)

    def test_files_route_without_chat_and_no_langfuse_keycloak_dependency(self):
        self.select("langfuse")
        self.chart("rook-ceph")
        self.policy["endpoints"]["librechat-files"] = {}
        self.write(self.client / "config/application-access.yaml", self.policy)
        self.assertEqual(set(self.derive()["endpoints"]), {"langfuse", "librechat-files"})

    def test_same_origin_grants(self):
        self.config["monitorLangfuseWrapper"]["hostname"] = "login.example.com"
        self.config["langfuse"]["langfuse"]["nextauth"]["url"] = "https://login.example.com"
        self.select("keycloak", "langfuse")
        self.policy["endpoints"] = {"keycloak": {"level": "restricted", "devices": ["device-a"]},
                                    "langfuse": {"level": "restricted", "devices": ["device-b"]}}
        self.write(self.client / "config/application-access.yaml", self.policy)
        with self.assertRaisesRegex(adapter.PlanError, "identical access and device grants"):
            self.derive()

    def test_cli_redaction_and_disclaimer(self):
        self.select("keycloak")
        args = ["--client-root", str(self.client), "--platform-root", str(self.platform)]
        for valid in (True, False):
            if not valid:
                self.write(self.client / "config/application-access.yaml", {"private-value.example.com": ["device-private"]})
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = adapter.main(args)
            self.assertEqual(code, 0 if valid else 1)
            output = out.getvalue() + err.getvalue()
            self.assertIn(adapter.DISCLAIMER, output)
            for private in ("example.com", "device-private", str(self.client), "Traceback"):
                self.assertNotIn(private, output)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            adapter.main(["--unknown", "private.example.com"])
        self.assertEqual(raised.exception.code, 2)
        self.assertNotIn("private.example.com", err.getvalue())
        self.assertIn(adapter.DISCLAIMER, err.getvalue())

    def test_local_revision_selection(self):
        self.mock_revisions.stop()
        comp = composition.Composition(self.client, self.platform, "prod-eu-1")
        source = next(o for o in comp.objects.values() if o["kind"] == "GitRepository")
        sha = "a" * 40
        for ref, match, dirty, succeeds in (({"branch": "main"}, True, False, True), ({"tag": "v1.2.3"}, True, False, True),
                ({"tag": "v1.2.3"}, False, False, False), ({"branch": "main"}, False, False, False),
                ({"tag": "v1.2.3"}, True, True, False), ({"branch": "candidate"}, True, False, False)):
            source["spec"]["ref"] = ref
            snapshot = SimpleNamespace(sha=sha, resolve=lambda ref: sha if match else "b" * 40, modified=lambda consumed=(): dirty)
            comp.platform_snapshot = snapshot
            with self.subTest(ref=ref, match=match, dirty=dirty), patch.object(adapter, "GitSnapshot", return_value=snapshot):
                if succeeds:
                    self.assertEqual(adapter.revisions(comp)["platform"], sha)
                else:
                    with self.assertRaises(adapter.PlanError):
                        adapter.revisions(comp)

    def test_source_identity_is_not_just_a_ref(self):
        self.mock_revisions.stop()
        comp = composition.Composition(self.client, self.platform, "prod-eu-1")
        obj = next(o for o in comp.objects.values() if o["kind"] == "GitRepository")
        for url, namespace in ((None, "flux-system"), ("https://example.com/other.git", "flux-system"), (adapter.BASE_URL, "other")):
            obj["spec"]["url"], obj["metadata"]["namespace"] = url, namespace
            with self.subTest(url=url, namespace=namespace), self.assertRaisesRegex(adapter.PlanError, "source URL or namespace"):
                adapter.revisions(comp)

    def test_bootstrap_names_do_not_hide_external_workloads(self):
        deployment = resource("Deployment", "source-controller", {"template": {"spec": {"containers": [{"name": "manager", "image": "nginx"}]}}})
        deployment["apiVersion"] = "apps/v1"
        service = resource("Service", "source-controller", {"type": "LoadBalancer", "ports": [{"port": 80}]})
        self.write(self.cluster / "flux-system/gotk-components.yaml", [deployment, service])
        self.write(self.cluster / "flux-system/kustomization.yaml", kustomization(["sync.yaml", "gotk-components.yaml"]))
        with self.assertRaisesRegex(adapter.PlanError, "untrusted Flux bootstrap"):
            self.derive()

    def test_alternate_ingress_is_not_silently_excluded(self):
        for fields in adapter.NO_INGRESS.values():
            for field in fields:
                node = self.config
                for part in field.split(".")[:-1]:
                    node = node.setdefault(part, {})
                node["enabled"] = False
        self.select("langfuse")
        for chart in ("kube-prometheus-stack", "opensearch", "openbao"):
            self.chart(chart)
        for chart in adapter.NO_INGRESS:
            path = self.platform / "releases/apps" / (chart + ".yaml")
            original = composition.document(composition.read(path))
            for field in adapter.NO_INGRESS[chart]:
                changed = copy.deepcopy(original)
                node = changed["spec"]["values"]
                for part in field.split(".")[:-1]:
                    node = node.setdefault(part, {})
                node["enabled"] = True
                node["hosts"] = ["extra.example.com"]
                self.write(path, changed)
                with self.subTest(field=field), self.assertRaisesRegex(adapter.PlanError, "alternate ingress/route"):
                    self.derive()
            self.write(path, original)

    def test_callback_registration_realm_matches_issuer(self):
        self.select(*adapter.BUNDLES)
        for name in ("librechat", "studio", "dify", "forgejo"):
            path = self.platform / "releases/apps" / ("keycloak-oidc-" + name + ".yaml")
            original = composition.document(composition.read(path))
            changed = copy.deepcopy(original)
            changed["spec"]["values"] = {"authKeycloak": {"realm": "other"}}
            self.write(path, changed)
            with self.subTest(name=name), self.assertRaisesRegex(adapter.PlanError, "OIDC realm disagrees"):
                self.derive()
            self.write(path, original)


if __name__ == "__main__":
    unittest.main()
