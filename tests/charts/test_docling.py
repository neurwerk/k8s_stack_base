"""Offline rendering and bootstrap contracts; no image, model or cluster calls."""

import builtins
import contextlib
import io
import json
import logging
import os
import subprocess
import sys
import types
import unittest
import yaml
from unittest.mock import patch, Mock, mock_open

from helm import ROOT, env_value, render, resource, resources


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
                result = render(chart, {"docling": {"enabled": False}})
                self.assertNotIn("EXTPROC_DOCLING__", disabled)
                self.assertNotIn("monitor-agentgateway-extproc-docling-secret", disabled)
                self.assertEqual(env_value(result, "EXTPROC_MAX_REQUEST_BYTES"), "5242880")
                self.assertEqual(env_value(result, "EXTPROC_GRPC_MAX_RECEIVE_MESSAGE_BYTES"), "6356992")
                self.assertEqual(env_value(result, "EXTPROC_GRPC_MAXIMUM_CONCURRENT_RPCS"), "4")
                self.assertIn("memory: 256Mi", disabled)
                self.assertIn("memory: 512Mi", disabled)
            else:
                self.assertEqual(enabled.count("cert-manager-internal-docling-server"), 2)
                self.assertIn("matchNames: [docling]", enabled)

    def test_native_tls_config_and_disposable_runtime(self):
        gateway = render("agentgateway")
        policy = resource(gateway, "AgentgatewayPolicy", "infra-agentgateway-policy-extproc")
        processor = policy["spec"]["traffic"]["extProc"]["conditional"][0]["policy"]
        self.assertEqual(processor["backendRef"], {
            "group": "", "kind": "Service", "name": "monitor-agentgateway-extproc-service",
            "namespace": "monitor-agentgateway-extproc", "port": 9000,
        })
        self.assertEqual(processor["failureMode"], "FailClosed")
        backend = resource(gateway, "AgentgatewayPolicy", "infra-agentgateway-extproc-backend")
        self.assertEqual(backend["spec"]["backend"]["http"], {
            "version": "HTTP2", "requestTimeout": "1005s",
        })
        grant = resource(gateway, "ReferenceGrant", "infra-agentgateway-extproc")
        self.assertEqual(grant["metadata"]["namespace"], "monitor-agentgateway-extproc")
        result = render("docling", {"docling": {"inference": {"caConfigMap": "inference-ca"}}})
        settings = self.settings(result)
        preset = settings["custom_vlm_presets"]["default"]
        self.assertEqual(preset["engine_options"]["engine_type"], "api")
        self.assertEqual(preset["engine_options"]["headers"], {})
        self.assertEqual(preset["model_spec"]["response_format"], "doctags")
        for name, value in {
            "max_sources_per_request": 1,
            "eng_loc_num_workers": 1,
            "enable_ui": False,
            "enable_management_endpoints": False,
            "debug_error_details": False,
            "allow_custom_vlm_config": False,
            "enable_remote_services": True,
            "allowed_vlm_presets": ["images"],
            "allowed_vlm_engines": ["api"],
            "allowed_source_types": ["file"],
            "allowed_target_types": ["inbody"],
            "allowed_image_export_modes": ["placeholder"],
            "artifact_storage_enabled": False,
            "single_use_results": True,
        }.items():
            self.assertEqual(settings[name], value, name)
        (deployment,) = resources(result, "Deployment")
        self.assertIn("terminationGracePeriodSeconds: 390", deployment)
        for text in (
            "runAsUser: 1001",
            "runAsGroup: 1001",
            "fsGroup: 1001",
            "automountServiceAccountToken: false",
            "readOnlyRootFilesystem: true",
            "path: /livez",
            "path: /readyz",
            "scheme: HTTPS",
            "HF_HUB_OFFLINE",
            "TRANSFORMERS_OFFLINE",
            "REQUESTS_CA_BUNDLE",
            "mountPath: /scratch",
            "mountPath: /tmp",
            '"name":"docling-api"',
            '"name":"docling-inference"',
        ):
            self.assertIn(text, deployment)
        self.assertNotIn("mountPath: /opt/app-root", deployment)
        self.assertNotIn("nvidia.com", result.stdout)
        self.assertFalse(resources(result, "Secret"))
        self.assertFalse(resources(result, "PersistentVolumeClaim"))
        (service,) = resources(result, "Service")
        self.assertIn("type: ClusterIP", service)
        self.assertIn("port: 443, targetPort: https", service)
        (certificate,) = resources(result, "Certificate")
        self.assertIn("docling.docling.svc.cluster.local", certificate)
        self.assertIn("rotationPolicy: Always", certificate)
        policy, cleanup_policy = resources(result, "NetworkPolicy")
        self.assertIn('cidr: "10.20.30.40/32"', policy)
        self.assertIn("monitor-agentgateway-extproc", policy)
        self.assertNotIn("ipBlock", cleanup_policy)
        self.assertIn("ports: [{port: 5001, protocol: TCP}]", cleanup_policy)
        (cleanup,) = resources(result, "CronJob")
        for text in (
            "concurrencyPolicy: Forbid",
            "activeDeadlineSeconds: 60",
            "automountServiceAccountToken: false",
            "readOnlyRootFilesystem: true",
            "key: ca.crt",
            "runAsUser: 1001",
        ):
            self.assertIn(text, cleanup)
        self.assertNotIn("key: tls.key", cleanup)
        self.assertNotIn("DOCLING_INFERENCE_TOKEN", cleanup)
        self.assertEqual(
            resource(result, "CronJob")["spec"]["jobTemplate"]["spec"]["template"]["spec"][
                "containers"
            ][0]["image"],
            resource(result, "Deployment")["spec"]["template"]["spec"]["containers"][0]["image"],
        )

        extproc = render(
            "agentgateway-extproc",
            {
                "monitorAgentgatewayExtproc": {
                    "maxRequestBytes": "5242880",
                    "grpcMaxReceiveMessageBytes": "6356992",
                    "resources": {"requests": {"memory": "256Mi"}, "limits": {"memory": "512Mi"}},
                }
            },
        )
        for name, value in {
            "DOCLING__ENABLED": "true",
            "DOCLING__BASE_URL": "https://docling.docling.svc",
            "DOCLING__CA_CERT": "/var/run/pii-engine/tls/ca.crt",
            "DOCLING__INFERENCE_MODE": "remote",
            "DOCLING__MAX_RESPONSE_BYTES": "16777216",
            "MAX_REQUEST_BYTES": "67108864",
            "GRPC_MAX_RECEIVE_MESSAGE_BYTES": "68222976",
            "GRPC_MAXIMUM_CONCURRENT_RPCS": "4",
            "ENGINE__TIMEOUT": "615",
        }.items():
            self.assertEqual(env_value(extproc, f"EXTPROC_{name}"), value, name)
        (deployment,) = resources(extproc, "Deployment")
        self.assertIn(
            "reload: monitor-agentgateway-extproc-engine-client-tls,monitor-agentgateway-extproc-docling-secret",
            deployment,
        )
        self.assertRegex(
            deployment,
            r"name: EXTPROC_DOCLING__API_KEY\s+valueFrom:\s+secretKeyRef:\s+name: monitor-agentgateway-extproc-docling-secret\s+key: api-key",
        )
        for text in (
            "replicas: 2",
            "memory: 1Gi",
            "memory: 2Gi",
            "mountPath: /var/run/pii-engine/tls",
        ):
            self.assertIn(text, deployment)
        for text in (
            "EXTPROC_DOCLING__CLIENT_CERT",
            "EXTPROC_DOCLING__CLIENT_KEY",
            "docling-inference",
        ):
            self.assertNotIn(text, extproc.stdout)
        self.assertFalse(resources(extproc, "HorizontalPodAutoscaler"))
        self.assertFalse(resources(extproc, "Secret"))
        resized = render(
            "agentgateway-extproc",
            {
                "monitorAgentgatewayExtproc": {
                    "doclingResources": {
                        "requests": {"memory": "2Gi"},
                        "limits": {"memory": "4Gi"},
                    },
                }
            },
        )
        self.assertIn("memory: 4Gi", resources(resized, "Deployment")[0])

    @staticmethod
    def settings(result):
        return json.loads(resource(result, "ConfigMap")["data"]["settings.json"])

    def test_http_inference_requires_opt_in_and_matching_port(self):
        for authority, port in (("10.20.30.40:8080", 8080), ("10.20.30.40", 80)):
            url = f"http://{authority}/v1/chat/completions"
            inference = {"url": url, "port": port, "allowHttp": True}
            result = render("docling", {"docling": {"inference": inference}})
            preset = self.settings(result)["custom_vlm_presets"]["default"]
            self.assertEqual(preset["engine_options"]["url"], url)
            self.assertIn(f"port: {port}, protocol: TCP", resources(result, "NetworkPolicy")[0])
            self.assertIn("scheme: HTTPS", resources(result, "Deployment")[0])
            for override in ({"allowHttp": False}, {"port": 443}):
                self.assertNotEqual(render(
                    "docling", {"docling": {"inference": inference | override}}, check=False
                ).returncode, 0)

    def test_bad_enabled_settings_fail(self):
        for flag in ("false",):
            for chart in ("docling", "agentgateway-extproc", "agentgateway"):
                self.assertNotEqual(
                    render(chart, {"docling": {"enabled": flag}}, check=False).returncode, 0
                )
            self.assertNotEqual(
                render(
                    "librechat/shared",
                    {"frontendLibrechat": {"documentAttachments": {"enabled": flag}}},
                    check=False,
                ).returncode,
                0,
            )
        bad = [
            {"inference": {"allowHttp": "true"}},
            {"inference": {"mode": "auto"}},
            {"inference": {"url": "http://inference.test/v1/chat/completions"}},
            {"inference": {"url": "https://user@inference.test/v1/chat/completions"}},
            {"inference": {"url": "https://inference.test/v1/chat/completions?token=x"}},
            {"inference": {"url": "https://inference.test/v1/chat/completions#fragment"}},
            {"inference": {"url": "https://inference.test/v1"}},
            {"inference": {"port": 8443}},
            {"inference": {"model": ""}},
            {"inference": {"model": "alias\nheader"}},
            {"inference": {"cidrs": []}},
            {"inference": {"cidrs": ["0.0.0.0/0"]}},
            {"inference": {"cidrs": ["10.1.2.3/7"]}},
            {"inference": {"cidrs": ["192.168.999.1/32"]}},
            {"inference": {"timeoutSeconds": 301}},
            {"syncWaitSeconds": 300},
            {"documentTimeoutSeconds": 0},
            {"cleanup": {"retentionSeconds": 86400}},
            {"apiKeySecretRef": {"name": ""}},
            {"inference": {"tokenSecretRef": {"name": "docling-api", "key": "api-key"}}},
            {"resources": {"limits": {"nvidia.com/gpu": 1}}},
            {"resources": {"requests": {"memory": "0Mi"}}},
            {"scratchSizeLimit": "0Gi"},
        ]
        for values in bad:
            with self.subTest(values=values):
                self.assertNotEqual(
                    render("docling", {"docling": values}, check=False).returncode, 0
                )
        for limits in (
            {"fileBytes": 0},
            {"pages": -1},
            {"count": 1.5},
            {"totalBytes": 1},
            {"fileBytes": 41943041},
            {"totalBytes": 41943041},
            {"count": 21},
            {"pages": 1001},
        ):
            for chart in ("docling", "agentgateway-extproc"):
                self.assertNotEqual(
                    render(chart, {"documentAttachments": limits}, check=False).returncode, 0
                )
        settings = self.settings(
            render("docling", {"documentAttachments": {"fileBytes": 1048576, "pages": 10}})
        )
        self.assertEqual((settings["max_file_size"], settings["max_num_pages"]), (1048576, 10))
        for document_timeout, sync_wait in ((300, 600), (3600, 3660)):
            overridden = render(
                "docling",
                {
                    "docling": {
                        "documentTimeoutSeconds": document_timeout,
                        "syncWaitSeconds": sync_wait,
                    }
                },
            )
            self.assertEqual(self.settings(overridden)["max_sync_wait"], sync_wait)
            (deployment,) = resources(overridden, "Deployment")
            self.assertIn(f"terminationGracePeriodSeconds: {sync_wait + 30}", deployment)
        self.assertNotEqual(
            render("docling", {"docling": {"syncWaitSeconds": 3661}}, check=False).returncode, 0
        )
        for values in (
            {"inference": {"mode": "auto"}},
            {"documentTimeoutSeconds": 0},
            {"documentTimeoutSeconds": 3601},
            {"syncWaitSeconds": 300},
            {"syncWaitSeconds": 3661},
        ):
            self.assertNotEqual(
                render("agentgateway-extproc", {"docling": values}, check=False).returncode, 0
            )
        for rpcs in (0, 129, True):
            self.assertNotEqual(
                render(
                    "agentgateway-extproc",
                    {
                        "monitorAgentgatewayExtproc": {"grpcMaximumConcurrentRpcs": rpcs},
                    },
                    check=False,
                ).returncode,
                0,
            )
        self.assertNotEqual(
            render(
                "agentgateway-extproc",
                {
                    "monitorAgentgatewayExtproc": {"replicas": 3},
                },
                check=False,
            ).returncode,
            0,
        )
        maximums = {"fileBytes": 41943040, "totalBytes": 41943040, "count": 20, "pages": 1000}
        for chart in ("docling", "agentgateway-extproc"):
            render(chart, {"documentAttachments": maximums})
        extproc = render(
            "agentgateway-extproc",
            {
                "docling": {
                    "documentTimeoutSeconds": 3600,
                    "syncWaitSeconds": 3660,
                },
                "documentAttachments": maximums,
            },
        )
        self.assertEqual(env_value(extproc, "EXTPROC_DOCLING__TIMEOUT"), "3660")
        self.assertEqual(env_value(extproc, "EXTPROC_DOCLING__DOCUMENT_TIMEOUT"), "3600")
        for name, value in zip(("FILE_BYTES", "TOTAL_BYTES", "COUNT", "PAGES"), maximums.values()):
            self.assertEqual(env_value(extproc, f"EXTPROC_DOCLING__{name}"), str(value))
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
                self.assertEqual(presets["images"]["engine_options"]["headers"],
                                 {"Authorization": "Bearer test-upstream-token"})
                self.assertEqual(presets["images"]["scale"], 1.0)
                self.assertIsNone(presets["images"]["max_size"])
                self.assertEqual(presets["default"]["scale"], 2.0)
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
        extproc = render("agentgateway-extproc", {"docling": {"inference": {"mode": "cpu"}}})
        self.assertEqual(env_value(extproc, "EXTPROC_DOCLING__INFERENCE_MODE"), "cpu")
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
        for text in ("docling-inference", "inferenceToken", "/external", "dataFrom:"):
            self.assertNotIn(text, delivery.stdout)

    def test_secret_sync_security(self):
        delivery = subprocess.run(
            ["kustomize", "build", str(ROOT / "releases/docling/secret-sync")],
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertFalse(resources(delivery, "Secret"))
        self.assertNotIn("dataFrom:", delivery.stdout)
        self.assertNotIn("template:", delivery.stdout)
        accounts = [yaml.safe_load(doc) for doc in resources(delivery, "ServiceAccount")]
        stores = [yaml.safe_load(doc) for doc in resources(delivery, "SecretStore")]
        for namespace in ("docling", "monitor-agentgateway-extproc"):
            (account,) = [item for item in accounts if item["metadata"]["namespace"] == namespace]
            self.assertFalse(account["automountServiceAccountToken"])
            (store,) = [item for item in stores if item["metadata"]["namespace"] == namespace]
            provider = store["spec"]["provider"]["vault"]
            self.assertEqual(provider["auth"]["kubernetes"]["role"], namespace)
            self.assertEqual(
                provider["auth"]["kubernetes"]["serviceAccountRef"]["name"],
                account["metadata"]["name"],
            )
            self.assertEqual(
                provider["caProvider"],
                {"type": "ConfigMap", "name": "infra-openbao-ca-bundle", "key": "ca.crt"},
            )

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
        self.assertNotIn("imageOutputType:", disabled)
        values = {"frontendLibrechat": {"documentAttachments": {"enabled": True}}}
        enabled = render("librechat/shared", values).stdout
        for text in ("fileConfig:", "AgentGateway:", "fileLimit: 5", "fileSizeLimit: 20",
                     "totalSizeLimit: 40", "fallback: provider", "'^application/pdf$'", "'^text/csv$'"):
            self.assertIn(text, enabled)
        self.assertNotIn("'^image/", enabled)
        values["documentAttachments"] = {"fileBytes": 1048576, "totalBytes": 2097152, "count": 2}
        changed = render("librechat/shared", values).stdout
        self.assertIn("fileSizeLimit: 1", changed)
        self.assertIn("totalSizeLimit: 2", changed)
        self.assertIn("fileLimit: 2", changed)
        values["frontendLibrechat"]["documentAttachments"]["imagesEnabled"] = True
        for version in (1, 2, "3"):
            values["guardrails"] = {"llmPolicyEngine": {"attachmentPolicyVersion": version}}
            self.assertIn("image uploads require", render("librechat/shared", values, check=False).stderr)
        values["guardrails"]["llmPolicyEngine"]["attachmentPolicyVersion"] = 3
        images = render("librechat/shared", values).stdout
        for text in ("imageOutputType: png", "'^image/jpeg$'", "'^image/png$'", "'^image/heic$'"):
            self.assertIn(text, images)
        self.assertNotIn("'^image/webp$'", images)
        values["frontendLibrechat"]["documentAttachments"]["enabled"] = False
        self.assertIn("image uploads require", render("librechat/shared", values, check=False).stderr)
