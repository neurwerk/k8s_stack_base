"""Rendered contracts for client-owned OpenRouter catalogs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"


def catalog(name: str = "remote/openrouter/acme/model", upstream: str = "acme/model") -> dict:
    return {
        "enabled": True,
        "excludedModels": [],
        "grantToAccessGroups": True,
        "models": [
            {
                "name": name,
                "upstreamModel": upstream,
                "label": "Friendly Model",
                "group": "Remote-OpenRouter-Acme",
            }
        ],
    }


def render(
    chart: str,
    values: dict,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as value_file:
        json.dump(values, value_file)
        value_file.flush()
        command = [
            "helm",
            "template",
            "catalog-test",
            str(ROOT / "charts" / chart),
            "--namespace",
            "catalog-test",
            "--values",
            str(LINT_VALUES),
            "--values",
            value_file.name,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=os.environ,
        )
    if check and result.returncode:
        raise AssertionError(result.stderr + result.stdout)
    return result


def resources(result: subprocess.CompletedProcess[str], kind: str) -> list[str]:
    return [
        document
        for document in re.split(r"(?m)^---\s*$", result.stdout)
        if re.search(rf"(?m)^kind:\s*{re.escape(kind)}\s*$", document)
    ]


def env_value(result: subprocess.CompletedProcess[str], name: str) -> str:
    match = re.search(
        rf'(?m)^\s+- name: {re.escape(name)}\n\s+value: (?P<value>".*")$',
        result.stdout,
    )
    if match is None:
        raise AssertionError(f"missing environment variable {name}")
    return json.loads(match.group("value"))


def resource_name(document: str) -> str:
    match = re.search(r"(?m)^metadata:\n\s+name:\s+([^\s]+)$", document)
    if match is None:
        raise AssertionError("resource has no metadata.name")
    return match.group(1)


def values_from(path: str) -> list[tuple[str, str]]:
    text = (ROOT / "releases" / path).read_text()
    return re.findall(
        r"(?m)^    - kind: (ConfigMap|Secret)\n      name: ([^\s]+)$", text
    )


def agent_values(openrouter: dict, client_models: list[dict] | None = None) -> dict:
    return {
        "openrouterCatalog": openrouter,
        "guardrails": {
            "llmPolicyEngine": {
                "enabled": True,
                "models": client_models or [],
                "localTarget": {
                    "name": "local-fallback",
                    "model": "local-model",
                    "provider": "Custom",
                    "custom": {"formats": [{"type": "Completions"}]},
                },
            }
        },
        "infraAgentgatewayWrapper": {
            "llamacpp": {"enabled": True, "host": "ollama.test", "port": 11434}
        },
        "authKeycloak": {"agentgatewayClientRoles": ["llm:invoke"]},
        "monitorPiiEngine": {"policy": {"routing": {"targets": []}}},
    }


class AgentGatewayCatalogTests(unittest.TestCase):
    def test_empty_base_catalog_is_safe_with_policy_engine_disabled(self) -> None:
        values = agent_values(
            {
                "enabled": False,
                "excludedModels": [],
                "grantToAccessGroups": False,
                "models": [],
            }
        )
        values["guardrails"]["llmPolicyEngine"]["enabled"] = False
        values["infraAgentgatewayWrapper"]["llamacpp"]["enabled"] = False
        result = render("agentgateway", values)
        self.assertEqual(resources(result, "AgentgatewayModel"), [])
        self.assertNotIn("infra-agentgateway-policy-extproc", result.stdout)
        parameters = resources(result, "AgentgatewayParameters")[0]
        self.assertNotIn("modelCatalog:", parameters)

    def test_inheritance_disabled_exclusion_and_legacy_replacement(self) -> None:
        inherited = render("agentgateway", agent_values(catalog()))
        inherited_models = resources(inherited, "AgentgatewayModel")
        self.assertEqual(len(inherited_models), 3)
        self.assertIn('expression: \'"acme/model"\'', inherited.stdout)
        self.assertIn("provider: Openrouter", inherited.stdout)

        excluded_catalog = catalog()
        excluded_catalog["excludedModels"] = ["acme/model"]
        excluded = render("agentgateway", agent_values(excluded_catalog))
        self.assertEqual(resources(excluded, "AgentgatewayModel"), [])

        disabled_catalog = catalog()
        disabled_catalog["enabled"] = False
        disabled = render("agentgateway", agent_values(disabled_catalog))
        self.assertEqual(resources(disabled, "AgentgatewayModel"), [])

        client_model = {
            "name": "remote/openrouter/acme/model",
            "provider": "DeepSeek",
            "model": "legacy-model",
            "baseURL": "https://legacy.test/v1",
        }
        legacy = render("agentgateway", agent_values(catalog(), [client_model]))
        legacy_models = resources(legacy, "AgentgatewayModel")
        self.assertEqual(len(legacy_models), 1)
        self.assertEqual(resource_name(legacy_models[0]), "remote-openrouter-acme-model")
        self.assertRegex(legacy_models[0], r"(?m)^  provider: DeepSeek$")

    def test_unknown_duplicate_exclusions_and_destination_cap_fail(self) -> None:
        unknown = catalog()
        unknown["excludedModels"] = ["missing/model"]
        result = render("agentgateway", agent_values(unknown), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown upstream model", result.stderr)

        duplicate = catalog()
        duplicate["excludedModels"] = ["acme/model", "acme/model"]
        result = render("agentgateway", agent_values(duplicate), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not contain duplicates", result.stderr)

        too_many = catalog()
        too_many["models"] = [
            {
                "name": f"remote/openrouter/acme/model-{index}",
                "upstreamModel": f"acme/model-{index}",
                "label": f"Model {index}",
                "group": "Remote-OpenRouter-Acme",
            }
            for index in range(257)
        ]
        result = render("agentgateway", agent_values(too_many), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("at most 256 destinations", result.stderr)

    def test_compact_pii_metadata_size_limit_fails_during_rendering(self) -> None:
        oversized = catalog()
        oversized["models"] = [
            {
                "name": f"remote/openrouter/{index:03d}/" + "x" * 96,
                "upstreamModel": f"acme/model-{index}",
                "label": f"Model {index}",
                "group": "Remote-OpenRouter-Acme",
            }
            for index in range(256)
        ]
        result = render("agentgateway", agent_values(oversized), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compact PII destination metadata JSON exceeds", result.stderr)

    def test_model_catalog_sources_are_optional_rendered_and_validated(self) -> None:
        values = agent_values({"enabled": False, "models": []})
        without_catalog = render("agentgateway", values)
        parameters = resources(without_catalog, "AgentgatewayParameters")[0]
        self.assertNotIn("modelCatalog:", parameters)

        values["infraAgentgatewayWrapper"]["modelCatalog"] = {
            "sources": [
                {"configMap": {"name": "client-model-pricing", "key": "catalog.json"}}
            ]
        }
        with_catalog = render("agentgateway", values)
        parameters = resources(with_catalog, "AgentgatewayParameters")[0]
        self.assertIn("modelCatalog:", parameters)
        self.assertIn("name: client-model-pricing", parameters)
        self.assertIn("key: catalog.json", parameters)

        for field, value in (("name", "Invalid_Name"), ("key", "invalid/key")):
            values["infraAgentgatewayWrapper"]["modelCatalog"]["sources"][0][
                "configMap"
            ][field] = value
            failed = render("agentgateway", values, check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn(f"configMap.{field}", failed.stderr)
            values["infraAgentgatewayWrapper"]["modelCatalog"]["sources"][0][
                "configMap"
            ][field] = "client-model-pricing" if field == "name" else "catalog.json"

    def test_local_fallback_is_required_only_for_pii_reroute(self) -> None:
        plain = agent_values(
            {"enabled": False, "models": []},
            [{"name": "remote/plain", "provider": "OpenAI", "model": "plain"}],
        )
        plain["guardrails"]["llmPolicyEngine"]["localTarget"] = {
            "name": "",
            "model": "",
            "provider": "",
            "custom": {},
        }
        plain["authKeycloak"]["agentgatewayClientRoles"].append(
            "model:remote/plain:invoke"
        )
        render("agentgateway", plain)

        plain["guardrails"]["llmPolicyEngine"]["models"][0]["piiReroute"] = True
        failed = render("agentgateway", plain, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("localTarget.name is required", failed.stderr)

    def test_long_names_are_hashed_without_renaming_existing_names(self) -> None:
        long_name = "remote/openrouter/publisher/" + "very-long-model-name-" * 4
        rendered = render("agentgateway", agent_values(catalog(long_name, "acme/long")))
        normalized = long_name.replace("/", "-")
        expected = f"{normalized[:41]}-{hashlib.sha256(normalized.encode()).hexdigest()[:8]}"
        names = {resource_name(item) for item in resources(rendered, "AgentgatewayModel")}
        self.assertIn(expected, names)
        self.assertIn(f"{expected}-remote", names)
        self.assertLessEqual(len(f"model-{expected}-remote"), 63)


class AuthorizationCatalogTests(unittest.TestCase):
    def test_dify_default_model_requires_client_context_size(self) -> None:
        permission = "model:remote/openrouter/acme/model:invoke"
        values = {
            "authKeycloak": {
                "difyAgentgatewayClientRoles": ["llm:invoke", permission],
            },
            "frontendDify": {
                "defaultModel": {"name": "remote/openrouter/acme/model"},
            },
        }

        for context_size in (None, ""):
            if context_size is not None:
                values["frontendDify"]["defaultModel"]["contextSize"] = context_size
            failed = render("dify/api", values, check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn(
                "frontendDify.defaultModel.contextSize is required",
                failed.stderr,
            )

        values["frontendDify"]["defaultModel"]["contextSize"] = "32768"
        rendered = render("dify/api", values)
        providers = json.loads(env_value(rendered, "MODEL_PROVIDER_CREDENTIALS"))
        self.assertEqual(
            providers["openai_api_compatible"]["credentials"]["context_size"],
            "32768",
        )

    def test_oidc_roles_are_derived_and_legacy_roles_are_deduplicated(self) -> None:
        values = {
            "openrouterCatalog": catalog(),
            "authKeycloak": {
                "agentgatewayClientRoles": [
                    "llm:invoke",
                    "model:remote/openrouter/acme/model:invoke",
                ]
            },
        }
        result = render("keycloak/oidc/agentgateway", values)
        roles = json.loads(env_value(result, "KC_CLIENT_ROLES"))
        self.assertEqual(roles.count("model:remote/openrouter/acme/model:invoke"), 1)

    def test_access_group_grants_can_be_disabled(self) -> None:
        values = {
            "openrouterCatalog": catalog(),
            "authKeycloak": {
                "agentgatewayClientRoles": ["llm:invoke"],
                "accessGroups": {"/access/llm": {"realmRoles": []}},
                "agentgatewayAccessGroups": {"/access/llm": ["llm:invoke"]},
            },
        }
        granted = json.loads(env_value(render("keycloak/realm-config/realm-roles", values), "KC_ACCESS_GROUPS"))
        self.assertIn(
            "model:remote/openrouter/acme/model:invoke",
            granted["/access/llm"]["clientRoles"]["agentgateway"],
        )

        values["openrouterCatalog"]["grantToAccessGroups"] = False
        explicit = json.loads(env_value(render("keycloak/realm-config/realm-roles", values), "KC_ACCESS_GROUPS"))
        self.assertEqual(
            explicit["/access/llm"]["clientRoles"]["agentgateway"], ["llm:invoke"]
        )

    def test_dify_and_bridge_validate_against_effective_roles(self) -> None:
        permission = "model:remote/openrouter/acme/model:invoke"
        values = {
            "openrouterCatalog": catalog(),
            "authKeycloak": {
                "agentgatewayClientRoles": ["llm:invoke"],
                "difyAgentgatewayClientRoles": ["llm:invoke", permission],
            },
            "frontendDify": {
                "defaultModel": {
                    "name": "remote/openrouter/acme/model",
                    "contextSize": "65536",
                }
            },
        }
        dify = render("dify/api", values)
        providers = json.loads(env_value(dify, "MODEL_PROVIDER_CREDENTIALS"))
        self.assertEqual(
            providers["openai_api_compatible"]["model"], "remote/openrouter/acme/model"
        )
        render("keycloak/oidc/dify-agentgateway", values)
        bridge = render("keycloak-api-key-bridge", values)
        match = re.search(r"(?m)^  primary\.json: >-\n    (?P<value>\{.*\})$", bridge.stdout)
        self.assertIsNotNone(match)
        self.assertIn(permission, json.loads(match.group("value"))["permissions"])

        values["openrouterCatalog"]["excludedModels"] = ["acme/model"]
        for chart in ("keycloak/oidc/dify-agentgateway", "keycloak-api-key-bridge"):
            result = render(chart, values, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("absent from authKeycloak.agentgatewayClientRoles", result.stderr)

        values["openrouterCatalog"]["excludedModels"] = []
        values["openrouterCatalog"]["enabled"] = False
        for chart in ("keycloak/oidc/dify-agentgateway", "keycloak-api-key-bridge"):
            result = render(chart, values, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("absent from authKeycloak.agentgatewayClientRoles", result.stderr)

    def test_access_group_environment_boundary(self) -> None:
        def boundary_values(over_limit: bool) -> tuple[dict, int]:
            realm_roles: list[str] = []
            access_groups = {
                "/access/llm": {
                    "realmRoles": realm_roles,
                    "clientRoles": {"agentgateway": ["llm:invoke"]},
                }
            }
            target = 120000 if over_limit else 115000
            while True:
                candidate = f"boundary-role-{len(realm_roles):04d}-" + "x" * 80
                realm_roles.append(candidate)
                size = len(json.dumps(access_groups, separators=(",", ":")))
                if size > target:
                    if not over_limit:
                        realm_roles.pop()
                        size = len(json.dumps(access_groups, separators=(",", ":")))
                    break
            values = {
                "openrouterCatalog": {
                    "enabled": False,
                    "excludedModels": [],
                    "grantToAccessGroups": True,
                    "models": [],
                },
                "authKeycloak": {
                    "agentgatewayClientRoles": ["llm:invoke"],
                    "accessGroups": {
                        "/access/llm": {"realmRoles": realm_roles},
                    },
                    "agentgatewayAccessGroups": {"/access/llm": ["llm:invoke"]},
                },
            }
            return values, size

        below_values, below_size = boundary_values(False)
        self.assertLessEqual(below_size, 120000)
        rendered = render("keycloak/realm-config/realm-roles", below_values)
        self.assertGreaterEqual(len(env_value(rendered, "KC_ACCESS_GROUPS")), below_size)
        self.assertLessEqual(len(env_value(rendered, "KC_ACCESS_GROUPS")), 120000)

        above_values, above_size = boundary_values(True)
        self.assertGreater(above_size, 120000)
        failed = render("keycloak/realm-config/realm-roles", above_values, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            "exceeds the 120000-byte process environment safety limit",
            failed.stderr,
        )


class LibreChatCatalogTests(unittest.TestCase):
    def test_model_specs_are_grouped_without_raw_fetched_rows(self) -> None:
        inherited = catalog()
        values = {
            "openrouterCatalog": inherited,
            "guardrails": {
                "llmPolicyEngine": {
                    "models": [
                        {"name": "local/llama", "local": True, "model": "llama"},
                        {
                            "name": "remote/deepseek/chat",
                            "provider": "DeepSeek",
                            "model": "chat",
                        },
                    ]
                }
            },
        }
        result = render("librechat/shared", values)
        self.assertIn("    modelSpecs:\n", result.stdout)
        self.assertIn("      enforce: false\n", result.stdout)
        self.assertIn("        - group: Remote-OpenRouter-Acme\n", result.stdout)
        self.assertIn("          name: remote/openrouter/acme/model\n", result.stdout)
        self.assertIn("        - group: Local\n", result.stdout)
        self.assertIn("          name: local/llama\n", result.stdout)
        self.assertIn("        - group: Remote-DeepSeek\n", result.stdout)
        self.assertIn("          name: remote/deepseek/chat\n", result.stdout)
        self.assertIn('            default: [""]\n', result.stdout)
        self.assertIn("            fetch: false\n", result.stdout)
        names = re.findall(r"(?m)^          name: ([^\s]+)$", result.stdout)
        self.assertEqual(len(names), len(set(names)))

        inherited["excludedModels"] = ["acme/model"]
        excluded = render("librechat/shared", values)
        self.assertNotIn("          name: remote/openrouter/acme/model\n", excluded.stdout)

    def test_model_catalog_cap_matches_extproc(self) -> None:
        inherited = catalog()
        inherited["models"] = [
            {
                "name": f"remote/openrouter/acme/model-{index}",
                "upstreamModel": f"acme/model-{index}",
                "label": f"Model {index}",
                "group": "Remote-OpenRouter-Acme",
            }
            for index in range(257)
        ]
        failed = render(
            "librechat/shared", {"openrouterCatalog": inherited}, check=False
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("at most 256 destinations", failed.stderr)


class CatalogOwnershipTests(unittest.TestCase):
    def test_base_catalog_is_empty_and_pricing_artifacts_are_absent(self) -> None:
        for path in (
            "releases/shared/openrouter-catalog.yaml",
            "releases/shared/openrouter-catalog-policy.json",
            "charts/agentgateway/files/catalog.json",
            "charts/agentgateway/files/catalog-overrides.json",
            "charts/agentgateway/templates/model-cost-catalog-configmap.yaml",
            "charts/agentgateway/templates/model-cost-catalog-overrides-configmap.yaml",
        ):
            self.assertFalse((ROOT / path).exists(), path)

        dify_values = (ROOT / "charts/dify/api/values.yaml").read_text()
        self.assertIn('defaultModel:\n    name: ""', dify_values)
        self.assertNotIn("remote/openrouter/", dify_values)
        for path in (
            "charts/pii-engine/values.yaml",
            "releases/shared/default-pii-settings.yaml",
        ):
            self.assertNotIn("local/llama3.2-3b", (ROOT / path).read_text())

    def test_release_values_from_precedence_is_exact(self) -> None:
        expected = {
            "agentgateway/app.yaml": [
                ("ConfigMap", "base-shared-hostnames-config-map"),
                ("ConfigMap", "base-shared-resources-config-map"),
                ("ConfigMap", "base-shared-oidc-clients-config-map"),
                ("ConfigMap", "base-shared-mcp-config-map"),
                ("ConfigMap", "base-shared-pii-config-map"),
                ("ConfigMap", "client-openrouter-catalog-values"),
                ("Secret", "infra-agentgateway-secrets"),
                ("ConfigMap", "client-values"),
                ("ConfigMap", "agentgateway-product-values"),
            ],
            "keycloak/oidc-agentgateway.yaml": [
                ("ConfigMap", "base-shared-hostnames-config-map"),
                ("ConfigMap", "base-shared-resources-config-map"),
                ("ConfigMap", "base-shared-oidc-clients-config-map"),
                ("ConfigMap", "auth-keycloak-app-defaults"),
                ("ConfigMap", "client-openrouter-catalog-values"),
                ("Secret", "auth-keycloak-secrets"),
                ("ConfigMap", "client-values"),
                ("ConfigMap", "keycloak-product-values"),
            ],
            "keycloak/oidc-dify-agentgateway.yaml": [
                ("ConfigMap", "base-shared-oidc-clients-config-map"),
                ("ConfigMap", "auth-keycloak-app-defaults"),
                ("ConfigMap", "client-openrouter-catalog-values"),
                ("Secret", "auth-keycloak-secrets"),
                ("ConfigMap", "client-values"),
                ("ConfigMap", "keycloak-product-values"),
            ],
            "keycloak/realm-roles.yaml": [
                ("ConfigMap", "base-shared-oidc-clients-config-map"),
                ("ConfigMap", "auth-keycloak-app-defaults"),
                ("ConfigMap", "client-openrouter-catalog-values"),
                ("Secret", "auth-keycloak-secrets"),
                ("ConfigMap", "client-values"),
                ("ConfigMap", "keycloak-product-values"),
            ],
            "keycloak-api-key-bridge/app.yaml": [
                ("ConfigMap", "base-shared-hostnames-config-map"),
                ("ConfigMap", "base-shared-resources-config-map"),
                ("ConfigMap", "base-shared-oidc-clients-config-map"),
                ("ConfigMap", "auth-keycloak-api-key-bridge-app-defaults"),
                ("ConfigMap", "client-openrouter-catalog-values"),
                ("ConfigMap", "client-values"),
                ("ConfigMap", "keycloak-api-key-bridge-product-values"),
            ],
            "librechat/core/shared.yaml": [
                ("ConfigMap", "base-shared-hostnames-config-map"),
                ("ConfigMap", "base-shared-resources-config-map"),
                ("ConfigMap", "base-shared-mcp-config-map"),
                ("ConfigMap", "frontend-librechat-app-defaults"),
                ("ConfigMap", "client-openrouter-catalog-values"),
                ("Secret", "frontend-librechat-runtime-secret"),
                ("ConfigMap", "client-values"),
                ("ConfigMap", "librechat-agentgateway-model-values"),
                ("ConfigMap", "librechat-product-values"),
            ],
        }
        for path, sequence in expected.items():
            self.assertEqual(values_from(path), sequence, path)
            text = (ROOT / "releases" / path).read_text()
            self.assertRegex(
                text,
                r"name: client-openrouter-catalog-values\n"
                r"      valuesKey: values.yaml\n"
                r"      optional: true",
                path,
            )


class SyntheticClientCatalogIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client_catalog = catalog()
        cls.client_catalog["models"].append(
            {
                "name": "remote/openrouter/example/second",
                "upstreamModel": "example/second",
                "label": "Second Model",
                "group": "Remote-OpenRouter-Example",
            }
        )
        cls.public_names = {
            entry["name"] for entry in cls.client_catalog["models"]
        }
        cls.roles = {f"model:{name}:invoke" for name in cls.public_names}
        cls.configured_groups = (
            "/access/librechat-users",
            "/access/studio-users",
            "/access/dify-users",
        )

        cls.agentgateway = render(
            "agentgateway",
            agent_values(cls.client_catalog),
        )
        cls.oidc = render(
            "keycloak/oidc/agentgateway",
            {
                "openrouterCatalog": cls.client_catalog,
                "authKeycloak": {"agentgatewayClientRoles": ["llm:invoke"]},
            },
        )
        cls.realm = render(
            "keycloak/realm-config/realm-roles",
            {
                "authKeycloak": {
                    "agentgatewayClientRoles": ["llm:invoke"],
                    "agentgatewayAccessGroups": {
                        group: ["llm:invoke"] for group in cls.configured_groups
                    },
                },
                "openrouterCatalog": cls.client_catalog,
            },
        )
        sample_permission = f"model:{next(iter(cls.public_names))}:invoke"
        subset_values = {
            "openrouterCatalog": cls.client_catalog,
            "authKeycloak": {
                "agentgatewayClientRoles": ["llm:invoke"],
                "difyAgentgatewayClientRoles": ["llm:invoke", sample_permission],
            }
        }
        cls.dify = render(
            "keycloak/oidc/dify-agentgateway",
            subset_values,
        )
        cls.bridge = render(
            "keycloak-api-key-bridge",
            subset_values,
        )
        cls.librechat = render(
            "librechat/shared",
            {
                "openrouterCatalog": cls.client_catalog,
                "guardrails": {"llmPolicyEngine": {"models": []}},
            },
        )

    def test_client_catalog_has_end_to_end_model_role_and_group_parity(self) -> None:
        public_matches = {
            match
            for document in resources(self.agentgateway, "AgentgatewayModel")
            if re.search(r"(?m)^  visibility: Public$", document)
            for match in re.findall(r'(?m)^    model: "([^"]+)"$', document)
        }
        self.assertEqual(public_matches, self.public_names)

        model_documents = resources(self.agentgateway, "AgentgatewayModel")
        resource_names = [resource_name(document) for document in model_documents]
        labels = [
            re.search(r"(?m)^    app\.kubernetes\.io/name: ([^\s]+)$", document).group(1)
            for document in model_documents
        ]
        dns_subdomain = re.compile(
            r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
        )
        label_value = re.compile(r"^[a-z0-9]([-a-z0-9_.]*[a-z0-9])?$")
        self.assertEqual(len(resource_names), len(set(resource_names)))
        self.assertEqual(len(labels), len(set(labels)))
        for name in resource_names:
            self.assertLessEqual(len(name), 253)
            self.assertRegex(name, dns_subdomain)
            self.assertTrue(all(len(segment) <= 63 for segment in name.split(".")))
        for label in labels:
            self.assertLessEqual(len(label), 63)
            self.assertRegex(label, label_value)

        oidc_roles = set(json.loads(env_value(self.oidc, "KC_CLIENT_ROLES")))
        self.assertEqual(oidc_roles, self.roles | {"llm:invoke"})
        groups_json = env_value(self.realm, "KC_ACCESS_GROUPS")
        self.assertLessEqual(len(groups_json), 120000)
        groups = json.loads(groups_json)
        for group in self.configured_groups:
            self.assertEqual(
                set(groups[group]["clientRoles"]["agentgateway"]),
                self.roles | {"llm:invoke"},
            )
        service_roles = json.loads(env_value(self.dify, "KC_SERVICE_ACCOUNT_ROLES"))
        sample_role = f"model:{next(iter(self.public_names))}:invoke"
        self.assertIn(sample_role, {item["roleName"] for item in service_roles})
        primary_match = re.search(
            r"(?m)^  primary\.json: >-\n    (?P<value>\{.*\})$",
            self.bridge.stdout,
        )
        self.assertIsNotNone(primary_match)
        self.assertIn(
            sample_role,
            json.loads(primary_match.group("value"))["permissions"],
        )

    def test_client_catalog_librechat_specs_preserve_groups(self) -> None:
        config = self.librechat.stdout
        specs_block = config.split("    modelSpecs:\n", 1)[1].split("    endpoints:\n", 1)[0]
        specs = re.findall(
            r"(?m)^        - group: (.+)\n"
            r"          label: .*\n"
            r"          name: ([^\s]+)$",
            specs_block,
        )
        expected = {
            entry["name"]: entry["group"] for entry in self.client_catalog["models"]
        }
        self.assertEqual({name: group for group, name in specs}, expected)


if __name__ == "__main__":
    unittest.main()
