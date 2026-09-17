"""Offline rendering and bootstrap contracts; no image, model or cluster calls."""

import builtins
import contextlib
import io
import json
import logging
import os
import re
import subprocess
import sys
import types
import unittest
from unittest.mock import patch, Mock, mock_open

from test_openrouter_catalog import ROOT, render, resources


class DoclingTests(unittest.TestCase):
    def test_disabled_is_empty_and_peer_changes_are_gated(self):
        self.assertEqual(render("docling", {"docling": {"enabled": False}}).stdout.strip(), "")
        for chart in ("agentgateway-extproc", "cert-manager/approval-policy"):
            disabled = render(chart, {"docling": {"enabled": False}}).stdout
            enabled = render(chart, {}).stdout
            self.assertNotIn("cert-manager-internal-docling-server", disabled)
            self.assertNotIn("kubernetes.io/metadata.name: docling", disabled)
            if chart == "agentgateway-extproc":
                self.assertIn("port: 5001", enabled)
                self.assertEqual(resources(render(chart, {}), "Deployment"),
                                 resources(render(chart, {"docling": {"enabled": False}}), "Deployment"))
            else:
                self.assertEqual(enabled.count("cert-manager-internal-docling-server"), 2)
                self.assertIn("matchNames: [docling]", enabled)

    def test_native_tls_config_and_disposable_runtime(self):
        result = render("docling", {"docling": {"inference": {"caConfigMap": "inference-ca"}}})
        settings = self.settings(result)
        preset = settings["custom_vlm_presets"]["default"]
        self.assertEqual(preset["engine_options"], {
            "engine_type": "api", "url": "https://inference.example.test/v1/chat/completions",
            "params": {"model": "granite-docling"}, "headers": {}, "timeout": 90, "concurrency": 1,
        })
        self.assertEqual(preset["model_spec"]["response_format"], "doctags")
        self.assertEqual(preset["model_spec"]["max_new_tokens"], 8192)
        self.assertEqual(preset["scale"], 2)
        for name, value in {
            "max_file_size": 20971520, "max_num_pages": 200, "max_sources_per_request": 1,
            "max_document_timeout": 300, "max_sync_wait": 360, "eng_loc_num_workers": 1,
            "eng_loc_share_models": False, "load_models_at_boot": False,
            "enable_ui": False, "enable_management_endpoints": False, "show_version_info": False,
            "debug_error_details": False, "allow_custom_vlm_config": False,
            "enable_remote_services": True, "allowed_vlm_presets": [], "allowed_vlm_engines": ["api"],
            "allowed_source_types": ["file"], "allowed_target_types": ["inbody"],
            "allowed_image_export_modes": ["placeholder"], "artifact_storage_enabled": False,
            "single_use_results": True, "result_removal_delay": 60, "scratch_path": "/scratch",
        }.items():
            self.assertEqual(settings[name], value, name)
        deployment, = resources(result, "Deployment")
        self.assertIn("terminationGracePeriodSeconds: 390", deployment)
        for text in ("runAsUser: 1001", "runAsGroup: 1001", "fsGroup: 1001",
                     "automountServiceAccountToken: false", "readOnlyRootFilesystem: true",
                     "path: /livez", "path: /readyz", "scheme: HTTPS", "HF_HUB_OFFLINE",
                     "TRANSFORMERS_OFFLINE", "REQUESTS_CA_BUNDLE", "mountPath: /scratch",
                     "mountPath: /tmp", '"name":"docling-api"', '"name":"docling-inference"'):
            self.assertIn(text, deployment)
        self.assertNotIn("mountPath: /opt/app-root", deployment)
        self.assertNotIn("nvidia.com", result.stdout)
        self.assertFalse(resources(result, "Secret"))
        self.assertFalse(resources(result, "PersistentVolumeClaim"))
        service, = resources(result, "Service")
        self.assertIn("type: ClusterIP", service)
        self.assertIn("port: 443, targetPort: https", service)
        certificate, = resources(result, "Certificate")
        self.assertIn("docling.docling.svc.cluster.local", certificate)
        self.assertIn("duration: 2160h", certificate)
        self.assertIn("rotationPolicy: Always", certificate)
        policy, cleanup_policy = resources(result, "NetworkPolicy")
        self.assertIn('cidr: "10.20.30.40/32"', policy)
        self.assertIn("monitor-agentgateway-extproc", policy)
        self.assertNotIn("ipBlock", cleanup_policy)
        self.assertIn("ports: [{port: 5001, protocol: TCP}]", cleanup_policy)
        cleanup, = resources(result, "CronJob")
        for text in ("concurrencyPolicy: Forbid", "activeDeadlineSeconds: 60",
                     "automountServiceAccountToken: false", "readOnlyRootFilesystem: true",
                     "key: ca.crt", "runAsUser: 1001"):
            self.assertIn(text, cleanup)
        self.assertNotIn("key: tls.key", cleanup)
        self.assertNotIn("DOCLING_INFERENCE_TOKEN", cleanup)
        self.assertIn("v1.33.0@sha256:546cf392145a0a578f23e4250663a37a5fb727fe6b57fd163e301584ad8bc18c", cleanup)
        validation = subprocess.run(["kubeconform", "-strict", "-summary", "-ignore-missing-schemas"],
                                    input=result.stdout, text=True, capture_output=True)
        self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)

    @staticmethod
    def settings(result):
        line = re.search(r"(?m)^  settings.json: (.*)$", result.stdout)
        return json.loads(json.loads(line.group(1)))

    def test_bad_enabled_settings_fail(self):
        for flag in (0, "", "false"):
            self.assertNotEqual(render("docling", {"docling": {"enabled": flag}}, check=False).returncode, 0)
            self.assertNotEqual(render("librechat/shared", {"frontendLibrechat": {"documentAttachments": {"enabled": flag}}}, check=False).returncode, 0)
        bad = [
            *({"inference": {"mode": mode}} for mode in ("auto", "", False, None, 0)),
            {"inference": {"url": "http://inference.test/v1/chat/completions"}},
            {"inference": {"url": "https://user@inference.test/v1/chat/completions"}},
            {"inference": {"url": "https://inference.test/v1/chat/completions?token=x"}},
            {"inference": {"url": "https://inference.test/v1/chat/completions#fragment"}},
            {"inference": {"url": "https://inference.test/v1"}},
            {"inference": {"port": 8443}}, {"inference": {"model": ""}},
            {"inference": {"model": "alias\nheader"}}, {"inference": {"cidrs": []}},
            {"inference": {"cidrs": ["0.0.0.0/0"]}},
            {"inference": {"cidrs": ["10.1.2.3/7"]}},
            {"inference": {"cidrs": ["192.168.999.1/32"]}},
            {"inference": {"timeoutSeconds": 301}}, {"syncWaitSeconds": 300},
            {"documentTimeoutSeconds": 0}, {"cleanup": {"retentionSeconds": 86400}},
            {"apiKeySecretRef": {"name": ""}},
            {"inference": {"tokenSecretRef": {"name": "docling-api", "key": "api-key"}}},
            {"resources": {"limits": {"nvidia.com/gpu": 1}}},
            {"resources": {"requests": {"memory": "0Mi"}}}, {"scratchSizeLimit": "0Gi"},
        ]
        for values in bad:
            with self.subTest(values=values):
                self.assertNotEqual(render("docling", {"docling": values}, check=False).returncode, 0)
        for limits in ({"fileBytes": 0}, {"pages": -1}, {"count": 1.5}, {"totalBytes": 1}):
            self.assertNotEqual(render("docling", {"documentAttachments": limits}, check=False).returncode, 0)
        settings = self.settings(render("docling", {"documentAttachments": {"fileBytes": 1048576, "pages": 10}}))
        self.assertEqual((settings["max_file_size"], settings["max_num_pages"]), (1048576, 10))
        for document_timeout, sync_wait in ((300, 600), (3600, 3660)):
            overridden = render("docling", {"docling": {
                "documentTimeoutSeconds": document_timeout, "syncWaitSeconds": sync_wait,
            }})
            self.assertEqual(self.settings(overridden)["max_sync_wait"], sync_wait)
            deployment, = resources(overridden, "Deployment")
            self.assertIn(f"terminationGracePeriodSeconds: {sync_wait + 30}", deployment)
        self.assertNotEqual(render("docling", {"docling": {"syncWaitSeconds": 3661}}, check=False).returncode, 0)
        release = (ROOT / "releases/docling/app/app.yaml").read_text()
        rollout_minutes = int(re.search(r"(?m)^  timeout: (\d+)m$", release).group(1))
        self.assertGreater(rollout_minutes * 60, 3660 + 30 + 5 * 60)

    def test_bootstrap_orders_secret_injection_and_log_suppression(self):
        settings = self.settings(render("docling", {}))
        scope = {"__name__": "bootstrap_test"}
        exec(compile((ROOT / "charts/docling/files/bootstrap.py").read_text(), "bootstrap.py", "exec"), scope)
        app = types.ModuleType("docling_serve.app")
        app.create_app = Mock()
        uvicorn = types.ModuleType("uvicorn")
        uvicorn.run = Mock()
        real_import = builtins.__import__

        def checked_import(name, *args, **kwargs):
            if name == "docling_serve.app":
                self.assertEqual(logging.root.manager.disable, logging.CRITICAL)
                presets = json.loads(os.environ["DOCLING_SERVE_CUSTOM_VLM_PRESETS"])
                self.assertEqual(presets["default"]["engine_options"]["headers"],
                                 {"Authorization": "Bearer test-upstream-token"})
            return real_import(name, *args, **kwargs)

        previous_disable = logging.root.manager.disable
        try:
            with patch.dict(os.environ, {"DOCLING_SERVE_CONFIG_FILE": "/config/settings.json",
                                        "DOCLING_INFERENCE_TOKEN": "test-upstream-token",
                                        "DOCLING_SERVE_API_KEY": "test-api-key"}), \
                    patch.dict(sys.modules, {"docling_serve.app": app, "uvicorn": uvicorn}), \
                    patch("builtins.open", mock_open(read_data=json.dumps(settings))), \
                    patch("builtins.__import__", side_effect=checked_import):
                self.assertEqual(scope["main"](), 0)
            call = uvicorn.run.call_args
            self.assertTrue(call.kwargs["factory"])
            self.assertIsNone(call.kwargs["log_config"])
            self.assertFalse(call.kwargs["access_log"])
            self.assertEqual(call.kwargs["ssl_keyfile"], "/tls/tls.key")
            self.assertEqual(call.kwargs["workers"], 1)
            self.assertIsNone(call.kwargs["limit_concurrency"])
            self.assertEqual(call.kwargs["timeout_graceful_shutdown"], settings["max_sync_wait"])
            with patch.dict(os.environ, {"DOCLING_SERVE_CONFIG_FILE": "/config/settings.json",
                                        "DOCLING_INFERENCE_TOKEN": "test-upstream-token",
                                        "DOCLING_SERVE_API_KEY": "test-api-key"}), \
                    patch.dict(sys.modules, {"docling_serve.app": app, "uvicorn": uvicorn}), \
                    patch("builtins.open", mock_open(read_data=json.dumps({**settings, "max_sync_wait": 600}))):
                self.assertEqual(scope["main"](), 0)
                self.assertEqual(uvicorn.run.call_args.kwargs["timeout_graceful_shutdown"], 600)
            for token in ("", "\n", "bad\r\nheader"):
                error = io.StringIO()
                with patch.dict(os.environ, {"DOCLING_SERVE_CONFIG_FILE": "/config/settings.json",
                                            "DOCLING_INFERENCE_TOKEN": token,
                                            "DOCLING_SERVE_API_KEY": "test-api-key"}), \
                        patch("builtins.open", mock_open(read_data=json.dumps(settings))), \
                        contextlib.redirect_stderr(error):
                    self.assertEqual(scope["main"](), 1)
                self.assertEqual(error.getvalue(), "Docling startup failed\n")
        finally:
            logging.disable(previous_disable)

    def test_cpu_render_bootstrap_and_internal_delivery(self):
        result = render("docling", {"docling": {"inference": {
            "mode": "cpu", "caConfigMap": "stale-inference-ca",
        }}})
        settings = self.settings(result)
        self.assertFalse(settings["enable_remote_services"])
        self.assertEqual(settings["default_ocr_preset"], "rapidocr")
        for name in ("custom_vlm_presets", "allowed_vlm_presets", "allowed_vlm_engines"):
            self.assertFalse(settings[name])
        self.assertNotIn("allowed_pipelines", settings)
        deployment, = resources(result, "Deployment")
        for text in ("DOCLING_INFERENCE_TOKEN", "docling-inference", "stale-inference-ca",
                     "REQUESTS_CA_BUNDLE", "inference-ca", "configmap.reloader"):
            self.assertNotIn(text, deployment)
        for text in ("{name: DOCLING_DEVICE, value: cpu}", '"docling-tls,docling-api"',
                     "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "readOnlyRootFilesystem: true",
                     "mountPath: /scratch", "mountPath: /tmp", "terminationGracePeriodSeconds: 390"):
            self.assertIn(text, deployment)
        self.assertNotIn("mountPath: /opt/app-root", deployment)
        self.assertFalse(resources(result, "PersistentVolumeClaim"))
        policy, _ = resources(result, "NetworkPolicy")
        self.assertEqual(policy.split("  egress:")[1].count("- to:"), 1)
        self.assertNotIn("ipBlock", policy)
        cpu = {"inference": {"mode": "cpu", **dict.fromkeys(
            ("url", "model", "tokenSecretRef", "cidrs", "port", "timeoutSeconds", "caConfigMap"))}}
        self.assertEqual(self.settings(render("docling", {"docling": cpu})), settings)
        for invalid in ({"apiKeySecretRef": {"name": ""}}, {"documentTimeoutSeconds": 0},
                        {"resources": {"limits": {"nvidia.com/gpu": 1}}}):
            self.assertNotEqual(render("docling", {"docling": {**cpu, **invalid}}, check=False).returncode, 0)

        scope = {"__name__": "bootstrap_test"}
        exec(compile((ROOT / "charts/docling/files/bootstrap.py").read_text(), "bootstrap.py", "exec"), scope)
        app, uvicorn = types.ModuleType("docling_serve.app"), types.ModuleType("uvicorn")
        app.create_app, uvicorn.run = Mock(), Mock()
        previous_disable = logging.root.manager.disable
        try:
            with patch.dict(os.environ, {"DOCLING_SERVE_CONFIG_FILE": "/config/settings.json",
                                         "DOCLING_SERVE_API_KEY": "test-api-key"}, clear=True), \
                    patch.dict(sys.modules, {"docling_serve.app": app, "uvicorn": uvicorn}), \
                    patch("builtins.open", mock_open(read_data=json.dumps(settings))):
                self.assertEqual(scope["main"](), 0)
                self.assertNotIn("DOCLING_SERVE_CUSTOM_VLM_PRESETS", os.environ)
                self.assertEqual(logging.root.manager.disable, logging.CRITICAL)
                self.assertFalse(uvicorn.run.call_args.kwargs["access_log"])
                self.assertEqual(uvicorn.run.call_args.kwargs["timeout_graceful_shutdown"], 360)
                os.environ["DOCLING_SERVE_API_KEY"] = ""
                with contextlib.redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(scope["main"](), 1)
                self.assertEqual(error.getvalue(), "Docling startup failed\n")
        finally:
            logging.disable(previous_disable)

        delivery = subprocess.run(["kustomize", "build", str(ROOT / "releases/docling/secret-sync/internal")],
                                  text=True, capture_output=True, check=True)
        self.assertEqual([len(resources(delivery, kind)) for kind in
                          ("ServiceAccount", "SecretStore", "ExternalSecret")], [2, 2, 2])
        self.assertEqual(len(re.findall(r"(?m)^kind:", delivery.stdout)), 6)
        for text in ("docling-inference", "inferenceToken", "/external", "dataFrom:"):
            self.assertNotIn(text, delivery.stdout)
        root = subprocess.run(["kustomize", "build", str(ROOT / "releases/docling/secret-sync")],
                              text=True, capture_output=True, check=True)
        for kind in ("ServiceAccount", "SecretStore", "ExternalSecret"):
            for resource in resources(delivery, kind):
                self.assertIn(resource.strip(), [item.strip() for item in resources(root, kind)])

    def test_optional_packages_and_shared_caps(self):
        for stage in ("namespaces", "infrastructure", "applications"):
            result = subprocess.check_output(["kustomize", "build", "--load-restrictor",
                                              "LoadRestrictionsNone", str(ROOT / "releases" / stage)], text=True)
            self.assertNotRegex(result, r"(?m)^  (?:name|namespace): docling$")
            self.assertNotIn("name: monitor-agentgateway-extproc-docling-secret", result)
            self.assertNotIn("name: monitor-agentgateway-extproc-openbao-secret-store", result)
        package = subprocess.check_output(["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone",
                                           str(ROOT / "releases/docling/app")], text=True)
        self.assertIn("name: base-shared-document-attachments-config-map", package)
        self.assertIn("name: docling-product-values", package)
        for source in ("release/config.yaml", "release/manifest.yaml"):
            text = (ROOT / source).read_text()
            for path in ("releases/docling/app", "releases/namespaces/docling", "releases/docling/reloader",
                         "releases/docling/secret-sync"):
                self.assertRegex(text, re.escape(path) + r"\n\s+status: excluded")

        delivery = subprocess.run(["kustomize", "build", str(ROOT / "releases/docling/secret-sync")],
                                  text=True, capture_output=True, check=True)
        self.assertEqual(len(re.findall(r"(?m)^kind:", delivery.stdout)), 7)
        self.assertFalse(resources(delivery, "Secret"))
        self.assertNotIn("dataFrom:", delivery.stdout)
        self.assertNotIn("template:", delivery.stdout)
        accounts = resources(delivery, "ServiceAccount")
        stores = resources(delivery, "SecretStore")
        secrets = resources(delivery, "ExternalSecret")
        self.assertEqual((len(accounts), len(stores), len(secrets)), (2, 2, 3))
        for namespace, source in (("docling", "docling/namespace.yaml"),
                                  ("monitor-agentgateway-extproc", "agentgateway-extproc.yaml")):
            account, = [item for item in accounts if f"  namespace: {namespace}\n" in item]
            self.assertIn(f"  name: {namespace}-external-secrets\n", account)
            self.assertIn("automountServiceAccountToken: false", account)
            store, = [item for item in stores if f"  namespace: {namespace}\n" in item]
            for text in (f"name: {namespace}-openbao-secret-store\n", f"role: {namespace}\n",
                         f"name: {namespace}-external-secrets\n", "mountPath: kubernetes",
                         "server: https://infra-openbao.infra-openbao.svc:8200", "path: secret",
                         "version: v2", "type: ConfigMap", "name: infra-openbao-ca-bundle",
                         "key: ca.crt", "audiences:\n", "- openbao"):
                self.assertIn(text, store)
            self.assertIn('secrets.neurwerk.com/openbao-trust: "true"',
                          (ROOT / "releases/namespaces" / source).read_text())
        for namespace, name, key, record, field in (
            ("docling", "docling-api", "api-key", "internal", "apiKey"),
            ("docling", "docling-inference", "token", "external", "inferenceToken"),
            ("monitor-agentgateway-extproc", "monitor-agentgateway-extproc-docling-secret",
             "api-key", "internal", "doclingApiKey"),
        ):
            secret, = [item for item in secrets if f"  name: {name}\n" in item]
            self.assertIn(f"  namespace: {namespace}\n", secret)
            self.assertEqual(secret.count("secretKey:"), 1)
            for text in (f"secretKey: {key}\n", f"key: {namespace}/{record}\n", f"property: {field}\n",
                         f"name: {namespace}-openbao-secret-store\n", "kind: SecretStore",
                         f"    name: {name}\n", "refreshInterval: 1h", "creationPolicy: Owner",
                         "deletionPolicy: Retain"):
                self.assertIn(text, secret)

        watcher_values = json.loads((ROOT / "releases/docling/reloader/values.json").read_text())
        default_reloader = render("reloader", {}).stdout
        selected_reloader = render("reloader", watcher_values).stdout
        def watched(output):
            return set(re.search(r'--namespaces=([^"\s]+)', output).group(1).split(","))
        self.assertEqual(watched(selected_reloader), watched(default_reloader) | {"docling"})
        def rbac_namespaces(output):
            return set(re.findall(r"(?m)^  namespace: (.+)$", output))
        self.assertEqual(rbac_namespaces(selected_reloader), rbac_namespaces(default_reloader) | {"docling"})
        reloader_values = subprocess.check_output(
            ["kustomize", "build", str(ROOT / "releases/docling/reloader")], text=True)
        self.assertIn("name: docling-reloader-values", reloader_values)
        self.assertIn("namespace: infra-reloader", reloader_values)
        release = (ROOT / "releases/reloader/app.yaml").read_text()
        self.assertIn("name: docling-reloader-values", release)
        self.assertIn("optional: true", release)

    def test_cleanup_uses_verified_scoped_request_and_fixed_errors(self):
        scope = {"__name__": "cleanup_test"}
        exec(compile((ROOT / "charts/docling/files/cleanup.py").read_text(), "cleanup.py", "exec"), scope)
        opener = Mock()
        response = Mock(status=200)
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.dict(os.environ, {"DOCLING_SERVE_API_KEY": "test-api-key", "RESULT_RETENTION_SECONDS": "600"}), \
                patch("ssl.create_default_context") as context, \
                patch("urllib.request.build_opener", return_value=opener):
            self.assertEqual(scope["main"](), 0)
            context.assert_called_once_with(cafile="/tls/ca.crt")
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://docling.docling.svc:443/v1/clear/results?older_then=600")
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.get_header("X-api-key"), "test-api-key")
            response.read.assert_not_called()
            opener.open.side_effect = RuntimeError("sensitive upstream response")
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(scope["main"](), 1)
            self.assertEqual(error.getvalue(), "Docling result cleanup failed\n")

    def test_librechat_opt_in_uses_mib_and_preserves_disabled_output(self):
        disabled = render("librechat/shared", {}).stdout
        self.assertNotIn("fileConfig:", disabled)
        values = {"frontendLibrechat": {"documentAttachments": {"enabled": True}}}
        enabled = render("librechat/shared", values).stdout
        for text in ("fileConfig:", "AgentGateway:", "fileLimit: 5", "fileSizeLimit: 20",
                     "totalSizeLimit: 40", "fallback: provider", "'^application/pdf$'", "'^text/csv$'"):
            self.assertIn(text, enabled)
        values["documentAttachments"] = {"fileBytes": 1048576, "totalBytes": 2097152, "count": 2}
        changed = render("librechat/shared", values).stdout
        self.assertIn("fileSizeLimit: 1", changed)
        self.assertIn("totalSizeLimit: 2", changed)
        self.assertIn("fileLimit: 2", changed)
