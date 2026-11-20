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
    def test_standard_pipeline_queue_matches_benchmark_without_more_job_workers(self):
        settings = self.settings(render("docling"))
        self.assertEqual(settings["queue_max_size"], 100)
        self.assertEqual(settings["batch_polling_interval_seconds"], 0.5)
        self.assertEqual(
            [settings[f"{stage}_batch_size"] for stage in ("ocr", "layout", "table")],
            [4, 4, 4],
        )
        self.assertEqual(settings["eng_loc_num_workers"], 1)

    def test_external_serve_uses_private_origin_without_local_workloads(self):
        external = {
            "url": "https://docling.example.test:8443",
            "port": 8443, "cidrs": ["192.168.42.9/32"],
            "caConfigMap": "docling-ca",
        }
        values = {"docling": {"mode": "external", "external": external}}
        self.assertEqual(render("docling", values).stdout.strip(), "")
        self.assertNotIn("cert-manager-internal-docling-server",
                         render("cert-manager/approval-policy", values).stdout)
        result = render("agentgateway-extproc", values)
        self.assertEqual(env_value(result, "EXTPROC_DOCLING__BASE_URL"), external["url"])
        self.assertEqual(env_value(result, "EXTPROC_DOCLING__ALLOW_HTTP"), "false")
        self.assertNotIn("INFERENCE_MODE", result.stdout)
        deployment = resource(result, "Deployment")
        env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        token = next(item for item in env if item["name"] == "EXTPROC_DOCLING__API_KEY")
        self.assertEqual(token["valueFrom"]["secretKeyRef"],
                         {"name": "monitor-agentgateway-extproc-docling-secret", "key": "api-key"})
        self.assertEqual(env_value(result, "EXTPROC_DOCLING__CA_CERT"), "/var/run/docling/ca.crt")
        pod = deployment["spec"]["template"]["spec"]
        self.assertIn(
            {"name": "docling-ca", "mountPath": "/var/run/docling", "readOnly": True},
            pod["containers"][0]["volumeMounts"],
        )
        self.assertIn(
            {"name": "docling-ca", "configMap": {
                "name": "docling-ca", "items": [{"key": "ca.crt", "path": "ca.crt"}],
            }},
            pod["volumes"],
        )
        self.assertEqual(deployment["metadata"]["annotations"]["configmap.reloader.stakater.com/reload"],
                         "docling-ca")
        policy = resource(result, "NetworkPolicy")
        self.assertEqual([rule for rule in policy["spec"]["egress"]
                          if any("ipBlock" in peer for peer in rule["to"])], [{
            "to": [{"ipBlock": {"cidr": "192.168.42.9/32"}}],
            "ports": [{"port": 8443, "protocol": "TCP"}],
        }])
        self.assertNotIn("kubernetes.io/metadata.name: docling", result.stdout)
        trusted = render("agentgateway-extproc", {"docling": {
            "mode": "external", "external": external | {"caConfigMap": ""},
        }})
        self.assertNotIn("EXTPROC_DOCLING__CA_CERT", trusted.stdout)
        self.assertNotIn("name: docling-ca", trusted.stdout)
        for override in (
            {"url": "https://docling.example.test:8443/v1"},
            {"url": "https://docling.example.test:8443?token=x"},
            {"url": "https://docling.example.test:8443#fragment"},
            {"url": "https://user@docling.example.test:8443"},
            {"url": "http://docling.example.test:8443"},
            {"allowHttp": "true"}, {"url": ""}, {"port": True},
            {"port": 443}, {"cidrs": ["0.0.0.0/0"]},
            {"cidrs": []}, {"cidrs": ["10.1.2.3/7"]},
            {"cidrs": ["192.168.999.1/32"]}, {"caConfigMap": "../invalid"},
        ):
            with self.subTest(override=override):
                result = render("agentgateway-extproc", {"docling": {
                    "mode": "external", "external": external | override,
                }}, check=False)
                self.assertNotEqual(result.returncode, 0)

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
                self.assertEqual(env_value(result, "EXTPROC_GRPC_MAXIMUM_CONCURRENT_RPCS"), "64")
                self.assertIn("memory: 256Mi", disabled)
                self.assertIn("memory: 512Mi", disabled)
            else:
                self.assertEqual(enabled.count("cert-manager-internal-docling-server"), 2)
                self.assertIn("matchNames: [docling]", enabled)
        disabled_external = {"docling": {"enabled": False, "mode": "external", "external": {
            "url": "http://docling.example.test", "caConfigMap": "docling-ca",
        }}}
        self.assertEqual(render("docling", disabled_external).stdout.strip(), "")
        result = render("agentgateway-extproc", disabled_external)
        self.assertNotIn("EXTPROC_DOCLING__", result.stdout)
        self.assertNotIn("docling-ca", result.stdout)
        self.assertNotIn("monitor-agentgateway-extproc-docling-secret", result.stdout)

    def test_native_tls_config_and_disposable_runtime(self):
        gateway = render("agentgateway")
        policy = resource(gateway, "AgentgatewayPolicy", "ai-example-api-processing")
        processors = policy["spec"]["traffic"]["extProc"]["conditional"]
        self.assertTrue(processors)
        for branch in processors:
            processor = branch["policy"]
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
        result = render("docling")
        settings = self.settings(result)
        for name, value in {
            "max_sources_per_request": 1,
            "eng_loc_num_workers": 1,
            "enable_ui": False,
            "enable_management_endpoints": False,
            "debug_error_details": False,
            "allow_custom_vlm_config": False,
            "enable_remote_services": False,
            "allowed_vlm_presets": [],
            "allowed_vlm_engines": [],
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
            "mountPath: /scratch",
            "mountPath: /tmp",
            '"name":"docling-api"',
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
        self.assertNotIn("ipBlock", policy)
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
            "DOCLING__ALLOW_HTTP": "false",
            "DOCLING__MAX_RESPONSE_BYTES": "16777216",
            "MAX_REQUEST_BYTES": "67108864",
            "GRPC_MAX_RECEIVE_MESSAGE_BYTES": "68222976",
            "GRPC_MAXIMUM_CONCURRENT_RPCS": "64",
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

    def test_public_native_api_preserves_requests_and_limits_paths(self):
        external = {"url": "http://10.20.30.40:8011", "port": 8011,
                    "allowHttp": True, "cidrs": ["10.20.30.40/32"]}
        docling = {"mode": "external", "external": external,
                   "ingress": {"enabled": True, "host": "docling.example.test"}}
        values = {"docling": docling, "publicCertificates": {"useProduction": True}}
        result = render("docling", values, namespace="docling")
        self.assertFalse(resources(result, "Deployment"))
        self.assertFalse(resources(result, "CronJob"))
        self.assertFalse(resources(result, "Secret"))
        service = resource(result, "Service")
        self.assertEqual(service["metadata"]["name"], "docling-external")
        self.assertNotIn("selector", service["spec"])
        self.assertNotIn("externalName", service["spec"])
        endpoints = resource(result, "EndpointSlice")
        self.assertEqual(endpoints["metadata"]["labels"]["kubernetes.io/service-name"], "docling-external")
        managed_by = endpoints["metadata"]["labels"]["endpointslice.kubernetes.io/managed-by"]
        self.assertIsInstance(managed_by, str)
        self.assertLessEqual(len(managed_by), 63)
        self.assertRegex(managed_by, r"\A(?:[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?)?\Z")
        self.assertEqual(endpoints["addressType"], "IPv4")
        self.assertEqual(endpoints["endpoints"], [
            {"addresses": ["10.20.30.40"], "conditions": {"ready": True}},
        ])
        self.assertEqual(endpoints["ports"], [{"name": "http", "port": 8011, "protocol": "TCP"}])
        gateway = resource(result, "Gateway")
        self.assertEqual(gateway["metadata"]["namespace"], "docling")
        self.assertEqual(gateway["metadata"]["annotations"]["cert-manager.io/cluster-issuer"],
                         "letsencrypt-production-cluster-issuer")
        self.assertEqual(gateway["spec"]["gatewayClassName"], "traefik")
        listener, = gateway["spec"]["listeners"]
        self.assertEqual((listener["protocol"], listener["port"], listener["hostname"]),
                         ("HTTPS", 443, "docling.example.test"))
        self.assertEqual(listener["tls"]["certificateRefs"],
                         [{"name": "docling-public-tls", "kind": "Secret", "group": ""}])
        self.assertEqual(listener["allowedRoutes"]["namespaces"]["from"], "Same")
        route = resource(result, "HTTPRoute")
        self.assertEqual(route["spec"]["hostnames"], ["docling.example.test"])
        rule, = route["spec"]["rules"]
        self.assertEqual(rule["backendRefs"], [{"name": "docling-external", "port": 8011}])
        self.assertNotIn("filters", rule)
        self.assertEqual(rule["timeouts"], {"request": "360s", "backendRequest": "360s"})
        self.assertEqual({(match["method"], match["path"]["type"], match["path"]["value"])
                          for match in rule["matches"]}, {
            ("POST", "Exact", "/v1/convert/file"),
            ("POST", "Exact", "/v1/convert/source"),
            ("POST", "Exact", "/v1/convert/file/async"),
            ("POST", "Exact", "/v1/convert/source/async"),
            ("GET", "PathPrefix", "/v1/status/poll"),
            ("GET", "PathPrefix", "/v1/result"),
        })
        approval = render("cert-manager/approval-policy", values)
        policy = resource(approval, "CertificateRequestPolicy", "cert-manager-public-docling-production")
        self.assertEqual(policy["spec"]["allowed"]["dnsNames"],
                         {"values": ["docling.example.test"], "required": True})
        self.assertEqual(policy["spec"]["selector"]["namespace"], {"matchNames": ["docling"]})
        self.assertNotIn("cert-manager-internal-docling-server", approval.stdout)
        for override in ({"enabled": False}, {"ingress": {"enabled": False}}):
            disabled = {"docling": docling | override}
            self.assertEqual(render("docling", disabled).stdout.strip(), "")
            self.assertNotIn("cert-manager-public-docling", render("cert-manager/approval-policy", disabled).stdout)
        for override in (
            {"mode": "internal"}, {"ingress": {"enabled": True, "host": ""}},
            {"ingress": {"enabled": True, "host": "https://docling.example.test"}},
            *({"external": external | change} for change in (
                {"allowHttp": False}, {"port": 443}, {"cidrs": ["10.20.30.0/24"]},
                {"url": "http://10.20.30.41:8011"}, {"url": "http://8.8.8.8:8011", "cidrs": ["8.8.8.8/32"]},
                {"url": "https://10.20.30.40:8011"}, {"url": "http://docling.example.test:8011"},
            )),
        ):
            with self.subTest(override=override):
                self.assertNotEqual(render("docling", {"docling": docling | override}, check=False).returncode, 0)

    def test_http_external_serve_requires_opt_in_and_matching_port(self):
        for authority, port in (("10.20.30.40:8080", 8080), ("10.20.30.40", 80)):
            url = f"http://{authority}"
            external = {"url": url + "/", "port": port, "allowHttp": True,
                        "cidrs": ["10.20.30.40/32"]}
            result = render("agentgateway-extproc", {"docling": {"mode": "external", "external": external}})
            self.assertEqual(env_value(result, "EXTPROC_DOCLING__BASE_URL"), url)
            self.assertEqual(env_value(result, "EXTPROC_DOCLING__ALLOW_HTTP"), "true")
            self.assertIn({
                "to": [{"ipBlock": {"cidr": "10.20.30.40/32"}}],
                "ports": [{"port": port, "protocol": "TCP"}],
            }, resource(result, "NetworkPolicy")["spec"]["egress"])
            for override in ({"allowHttp": False}, {"port": 443}):
                self.assertNotEqual(render(
                    "agentgateway-extproc",
                    {"docling": {"mode": "external", "external": external | override}}, check=False
                ).returncode, 0)

    def test_bad_enabled_settings_fail(self):
        for flag in ("false",):
            for chart in ("docling", "agentgateway-extproc", "agentgateway"):
                self.assertNotEqual(
                    render(chart, {"docling": {"enabled": flag}}, check=False).returncode, 0
                )
        bad = [
            {"mode": "auto"},
            {"syncWaitSeconds": 300},
            {"documentTimeoutSeconds": 0},
            {"cleanup": {"retentionSeconds": 86400}},
            {"apiKeySecretRef": {"name": ""}},
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
            {"mode": "auto"},
            {"documentTimeoutSeconds": 0},
            {"documentTimeoutSeconds": 3601},
            {"syncWaitSeconds": 300},
            {"syncWaitSeconds": 3661},
        ):
            self.assertNotEqual(
                render("agentgateway-extproc", {"docling": values}, check=False).returncode, 0
            )
        for chart in ("docling", "agentgateway-extproc", "agentgateway"):
            for values in ({"mode": "private-vlm"}, {"inference": {"mode": "internal-standard"}}):
                self.assertNotEqual(render(chart, {"docling": values}, check=False).returncode, 0)
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
    def test_bootstrap_validates_api_key_and_suppresses_upstream_logs(self):
        settings = self.settings(render("docling", {}))
        scope = {"__name__": "bootstrap_test"}
        exec(compile((ROOT / "charts/docling/files/bootstrap.py").read_text(), "bootstrap.py", "exec"), scope)
        app = types.ModuleType("docling_serve.app")
        app.create_app = Mock()
        uvicorn = types.ModuleType("uvicorn")
        captured_logs = io.StringIO()
        logger = logging.getLogger("docling-bootstrap-test")
        previous_logger = (logger.handlers[:], logger.level, logger.propagate, logger.disabled)
        logger.handlers = [logging.StreamHandler(captured_logs)]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.disabled = False
        marker = "synthetic-inference-response-content"

        def emit_sensitive_logs(*args, **kwargs):
            logger.error("upstream ERROR %s", marker)
            logger.critical("upstream CRITICAL %s", marker)
            try:
                raise RuntimeError(marker)
            except RuntimeError:
                logger.exception("upstream exception")

        uvicorn.run = Mock(side_effect=emit_sensitive_logs)
        real_import = builtins.__import__

        def checked_import(name, *args, **kwargs):
            if name == "docling_serve.app":
                emit_sensitive_logs()
                # Import-time configuration must not reopen runtime logging.
                logging.disable(logging.NOTSET)
            return real_import(name, *args, **kwargs)

        previous_disable = logging.root.manager.disable
        try:
            logging.disable(logging.NOTSET)
            emit_sensitive_logs()
            self.assertIn(marker, captured_logs.getvalue())
            captured_logs.seek(0)
            captured_logs.truncate()
            with patch.dict(os.environ, {"DOCLING_SERVE_CONFIG_FILE": "/config/settings.json",
                                        "DOCLING_SERVE_API_KEY": "test-api-key"}), \
                    patch.dict(sys.modules, {"docling_serve.app": app, "uvicorn": uvicorn}), \
                    patch("builtins.open", mock_open(read_data=json.dumps(settings))), \
                    patch("builtins.__import__", side_effect=checked_import):
                self.assertEqual(scope["main"](), 0)
            self.assertEqual(captured_logs.getvalue(), "")
            call = uvicorn.run.call_args
            self.assertTrue(call.kwargs["factory"])
            self.assertIsNone(call.kwargs["log_config"])
            self.assertFalse(call.kwargs["access_log"])
            self.assertEqual(call.kwargs["ssl_keyfile"], "/tls/tls.key")
            self.assertEqual(call.kwargs["workers"], 1)
            self.assertIsNone(call.kwargs["limit_concurrency"])
            self.assertEqual(call.kwargs["timeout_graceful_shutdown"], settings["max_sync_wait"])
            with patch.dict(os.environ, {"DOCLING_SERVE_CONFIG_FILE": "/config/settings.json",
                                        "DOCLING_SERVE_API_KEY": "test-api-key"}), \
                    patch.dict(sys.modules, {"docling_serve.app": app, "uvicorn": uvicorn}), \
                    patch("builtins.open", mock_open(read_data=json.dumps({**settings, "max_sync_wait": 600}))):
                self.assertEqual(scope["main"](), 0)
                self.assertEqual(uvicorn.run.call_args.kwargs["timeout_graceful_shutdown"], 600)
            for token in ("", "\n", "bad\r\nheader"):
                error = io.StringIO()
                with patch.dict(os.environ, {"DOCLING_SERVE_CONFIG_FILE": "/config/settings.json",
                                            "DOCLING_SERVE_API_KEY": token}), \
                        patch("builtins.open", mock_open(read_data=json.dumps(settings))), \
                        contextlib.redirect_stderr(error):
                    self.assertEqual(scope["main"](), 1)
                self.assertEqual(error.getvalue(), "Docling startup failed\n")
            def fail_server(*args, **kwargs):
                emit_sensitive_logs()
                raise RuntimeError(marker)

            uvicorn.run.side_effect = fail_server
            with patch.dict(os.environ, {"DOCLING_SERVE_CONFIG_FILE": "/config/settings.json",
                                        "DOCLING_SERVE_API_KEY": "test-api-key"}), \
                    patch.dict(sys.modules, {"docling_serve.app": app, "uvicorn": uvicorn}), \
                    patch("builtins.open", mock_open(read_data=json.dumps(settings))), \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(scope["main"](), 1)
            self.assertEqual(error.getvalue(), "Docling startup failed\n")
            self.assertEqual(captured_logs.getvalue(), "")
        finally:
            logging.disable(previous_disable)
            logger.handlers, logger.level, logger.propagate, logger.disabled = previous_logger

    def test_cpu_render_bootstrap_and_internal_delivery(self):
        extproc = render("agentgateway-extproc")
        self.assertNotIn("INFERENCE_MODE", extproc.stdout)
        result = render("docling")
        settings = self.settings(result)
        self.assertFalse(settings["enable_remote_services"])
        self.assertEqual(settings["custom_ocr_presets"]["default"], {
            "kind": "rapidocr", "backend": "torch", "model_size": "small",
            "use_cls": True, "lang": ["ch"], "scale": 3.0,
        })
        self.assertEqual(settings["custom_table_structure_presets"]["default"], {
            "kind": "docling_tableformer", "mode": "accurate", "do_cell_matching": True,
        })
        self.assertEqual(env_value(result, "DOCLING_NUM_THREADS"), "4")
        self.assertEqual(settings["max_num_pages"], 2)
        self.assertEqual(env_value(extproc, "EXTPROC_DOCLING__PAGES"), "2")
        for pages in (1, 250, 1000):
            values = {"documentAttachments": {"pages": pages}}
            self.assertEqual(self.settings(render("docling", values))["max_num_pages"], pages)
            self.assertEqual(env_value(render("agentgateway-extproc", values), "EXTPROC_DOCLING__PAGES"), str(pages))
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
        external = subprocess.run(
            ["kustomize", "build", str(ROOT / "releases/docling/secret-sync/external")],
            text=True, capture_output=True, check=True,
        )
        self.assertFalse(resources(external, "Secret"))
        self.assertEqual(len(resources(external, "SecretStore")), 1)
        self.assertEqual(len(resources(external, "ServiceAccount")), 1)
        secret = resource(external, "ExternalSecret")
        self.assertEqual(secret["metadata"]["namespace"], "monitor-agentgateway-extproc")
        self.assertEqual(secret["spec"]["target"]["name"], "monitor-agentgateway-extproc-docling-secret")
        self.assertEqual(secret["spec"]["data"], [{
            "secretKey": "api-key",
            "remoteRef": {"key": "monitor-agentgateway-extproc/internal", "property": "doclingApiKey"},
        }])
        self.assertNotIn("docling-inference", delivery.stdout + external.stdout)

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
