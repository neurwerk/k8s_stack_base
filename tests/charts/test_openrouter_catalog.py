"""Rendered contracts for client-owned OpenRouter catalogs."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import yaml

from catalog import agent_values, catalog
from helm import ROOT, env_value, render, resource, resources


def resource_name(document: str) -> str:
    return yaml.safe_load(document)["metadata"]["name"]


class AgentGatewayCatalogTests(unittest.TestCase):
    def test_attachment_metadata_is_sparse_and_preserves_replacement(self) -> None:
        selected = catalog()
        name = selected["models"][0]["name"]
        direct = {"name": "Local/Mixed_Case", "provider": "OpenAI", "model": "plain"}
        values = agent_values(selected, [direct])
        values["authKeycloak"]["agentgatewayClientRoles"].append(f"model:{direct['name']}:invoke")

        text_only = render("agentgateway", {**values, "docling": {"enabled": False}}).stdout
        self.assertIn("maxBufferSize: 6291456", text_only)
        self.assertIn('requestTimeout: "630s"', text_only)
        for wait in (360, 600, 3660):
            documents = render("agentgateway", {**values, "docling": {
                "enabled": True, "syncWaitSeconds": wait,
            }}).stdout
            self.assertIn("maxBufferSize: 67108864", documents)
            self.assertIn(f'requestTimeout: "{wait + 615 + 30}s"', documents)
        for wait in (0, 3661, "360", True):
            self.assertNotEqual(render("agentgateway", {**values, "docling": {
                "enabled": True, "syncWaitSeconds": wait,
            }}, check=False).returncode, 0)

        def metadata() -> dict:
            result = render("agentgateway", values)
            return {
                key: json.loads(json.loads(value))
                for key, value in re.findall(
                    r'(?m)^                (contract_version|models|attachment_modes): (".*")$',
                    result.stdout,
                )
            }

        expected = {"contract_version": 1, "models": {name: True, direct["name"]: True}}
        self.assertEqual(metadata(), expected)
        for mode in ("block", "extract"):
            selected["models"][0]["attachmentMode"] = mode
            self.assertEqual(metadata(), {**expected, "attachment_modes": {name: mode}})
        for mode, pii in (("block", True), ("block", False), ("extract", True), ("extract", False), ("passthrough", False)):
            direct.update(attachmentMode=mode, piiEnabled=pii)
            expected["models"][direct["name"]] = pii
            self.assertEqual(metadata(), {
                **expected,
                "attachment_modes": {name: "extract", direct["name"]: mode},
            })

        direct.update(name=name)
        del direct["attachmentMode"]
        self.assertEqual(metadata(), {"contract_version": 1, "models": {name: False}})
        direct["attachmentMode"] = "passthrough"
        self.assertEqual(metadata(), {
            "contract_version": 1, "models": {name: False},
            "attachment_modes": {name: "passthrough"},
        })
        values["guardrails"]["llmPolicyEngine"]["models"] = []
        selected["excludedModels"] = ["acme/model"]
        self.assertNotIn("attachment_modes:", render("agentgateway", values).stdout)

    def test_invalid_attachment_modes_and_pii_constraint(self) -> None:
        for source in ("direct", "catalog"):
            for mode in (None, True, [], "unknown", "passthrough"):
                with self.subTest(source=source, mode=mode):
                    selected = catalog()
                    direct = {
                        "name": selected["models"][0]["name"],
                        "provider": "OpenAI",
                        "model": "plain",
                    }
                    row = direct if source == "direct" else selected["models"][0]
                    row["attachmentMode"] = mode
                    if source == "catalog":
                        row["piiEnabled"] = False  # Catalog PII overrides remain unsupported.
                    values = agent_values(selected, [direct] if source == "direct" else [])
                    failed = render("agentgateway", values, check=False)
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertIn("attachmentMode", failed.stderr)
                    if mode == "passthrough":
                        self.assertIn("requires piiEnabled:false", failed.stderr)

    def test_attachment_metadata_expression_size_limit(self) -> None:
        selected = catalog()
        selected["models"] = [
            {"name": f"remote/{index:03d}/" + "x" * 44, "upstreamModel": f"acme/{index}", "attachmentMode": "extract"}
            for index in range(256)
        ]
        pii = {row["name"]: True for row in selected["models"]}
        modes = {row["name"]: row["attachmentMode"] for row in selected["models"]}
        self.assertLessEqual(len(json.dumps(pii, separators=(",", ":"))), 16384)
        self.assertGreater(len(json.dumps(modes, separators=(",", ":"))), 16384)
        failed = render("agentgateway", agent_values(selected), check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("attachment mode destination metadata JSON exceeds", failed.stderr)
        selected["models"] = selected["models"][:240]
        render("agentgateway", agent_values(selected))

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

    def test_access_group_grants_are_explicit_and_catalog_roles_remain_available(self) -> None:
        group = "/access/neurwerk-llm-all-users"
        permission = "model:remote/openrouter/acme/model:invoke"
        values = {
            "openrouterCatalog": catalog(),
            "authKeycloak": {
                "agentgatewayClientRoles": ["llm:invoke"],
                "agentgatewayAccessGroups": {group: ["llm:invoke"]},
            },
        }
        explicit = json.loads(env_value(render("keycloak/realm-config/realm-roles", values), "KC_ACCESS_GROUPS"))
        self.assertEqual(
            explicit[group]["clientRoles"]["agentgateway"], ["llm:invoke"]
        )
        values["authKeycloak"]["agentgatewayAccessGroups"][group].append(permission)
        granted = json.loads(env_value(render("keycloak/realm-config/realm-roles", values), "KC_ACCESS_GROUPS"))
        self.assertEqual(granted[group]["clientRoles"]["agentgateway"], ["llm:invoke", permission])

        values["openrouterCatalog"]["excludedModels"] = ["acme/model"]
        failed = render("keycloak/realm-config/realm-roles", values, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("grants undeclared role", failed.stderr)

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
        self.assertIn(
            permission,
            json.loads(resource(bridge, "ConfigMap")["data"]["primary.json"])["permissions"],
        )

        for selection in ({"excludedModels": ["acme/model"]}, {"enabled": False}):
            values["openrouterCatalog"] = {**catalog(), **selection}
            for chart in ("keycloak/oidc/dify-agentgateway", "keycloak-api-key-bridge"):
                with (
                    self.subTest(chart=chart, selection=selection),
                    self.assertRaisesRegex(
                        AssertionError,
                        "absent from authKeycloak.agentgatewayClientRoles",
                    ),
                ):
                    render(chart, values)

    def test_access_group_environment_boundary(self) -> None:
        def boundary_values(over_limit: bool) -> tuple[dict, int]:
            roles = ["llm:invoke"]
            group = "/access/neurwerk-llm-all-users"
            access_groups = json.loads(env_value(
                render("keycloak/realm-config/realm-roles", {}), "KC_ACCESS_GROUPS"
            ))
            access_groups[group]["clientRoles"] = {"agentgateway": roles}
            target = 120000 if over_limit else 115000
            while True:
                candidate = f"model:boundary-{len(roles):04d}-" + "x" * 80 + ":invoke"
                roles.append(candidate)
                size = len(json.dumps(access_groups, separators=(",", ":")))
                if size > target:
                    if not over_limit:
                        roles.pop()
                        size = len(json.dumps(access_groups, separators=(",", ":")))
                    break
            values = {
                "openrouterCatalog": {
                    "enabled": False,
                    "excludedModels": [],
                    "grantToAccessGroups": False,
                    "models": [],
                },
                "authKeycloak": {
                    "agentgatewayClientRoles": roles,
                    "agentgatewayAccessGroups": {group: roles},
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
            "frontendLibrechat": {"agentGateway": {"defaultModel": "local/llama"}},
            "openrouterCatalog": inherited,
            "guardrails": {
                "llmPolicyEngine": {
                    "models": [
                        {"name": "local/llama", "local": True, "model": "llama"},
                        {
                            "name": "remote/deepseek/chat",
                            "provider": "DeepSeek",
                            "model": "chat",
                            "group": "Remote-DeepSeek-Direct",
                        },
                    ]
                }
            },
        }
        result = render("librechat/shared", values)
        config = yaml.safe_load(
            resource(result, "ConfigMap", "frontend-librechat-config-map")["data"]["librechat.yaml"]
        )
        self.assertFalse(config["interface"]["modelSelect"])
        self.assertFalse(config["modelSpecs"]["enforce"])
        specs = config["modelSpecs"]["list"]
        expected = {
            "remote/openrouter/acme/model": "Remote-OpenRouter-Acme",
            "local/llama": "Local",
            "remote/deepseek/chat": "Remote-DeepSeek-Direct",
        }
        self.assertEqual({row["name"]: row["group"] for row in specs}, expected)
        self.assertEqual(len(specs), len(expected))
        self.assertEqual([row["name"] for row in specs if row.get("default")], ["local/llama"])
        endpoint = next(
            row for row in config["endpoints"]["custom"] if row["name"] == "AgentGateway"
        )
        self.assertEqual(endpoint["models"], {"default": list(expected), "fetch": False})
        inherited["excludedModels"] = ["acme/model"]
        excluded = render("librechat/shared", values)
        self.assertNotIn("          name: remote/openrouter/acme/model\n", excluded.stdout)

    def test_librechat_rejects_unknown_hard_default(self) -> None:
        failed = render(
            "librechat/shared",
            {
                "frontendLibrechat": {
                    "agentGateway": {"defaultModel": "local/missing"}
                },
                "openrouterCatalog": catalog(),
            },
            check=False,
        )

        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(
            'frontendLibrechat.agentGateway.defaultModel "local/missing" is not in the effective model catalog',
            failed.stderr,
        )

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
    def test_client_values_override_optional_catalog(self) -> None:
        for path, product in (
            ("agentgateway/app.yaml", "agentgateway"),
            ("keycloak/oidc-agentgateway.yaml", "keycloak"),
            ("keycloak/oidc-dify-agentgateway.yaml", "keycloak"),
            ("keycloak/realm-roles.yaml", "keycloak"),
            ("keycloak-api-key-bridge/app.yaml", "keycloak-api-key-bridge"),
            ("librechat/core/shared.yaml", "librechat"),
        ):
            with self.subTest(release=path):
                release = yaml.safe_load((ROOT / "releases" / path).read_text())
                refs = release["spec"]["valuesFrom"]
                names = [ref["name"] for ref in refs]
                catalog_index = names.index("client-openrouter-catalog-values")
                client_index = names.index("client-values")
                self.assertEqual(
                    refs[catalog_index],
                    {
                        "kind": "ConfigMap",
                        "name": "client-openrouter-catalog-values",
                        "valuesKey": "values.yaml",
                        "optional": True,
                    },
                )
                self.assertLess(catalog_index, client_index)
                self.assertLess(client_index, names.index(f"{product}-product-values"))
                for index, ref in enumerate(refs):
                    if ref["kind"] == "ConfigMap" and (
                        ref["name"].startswith("base-shared-")
                        or ref["name"].endswith("-app-defaults")
                    ):
                        self.assertLess(index, catalog_index, ref["name"])
                    if ref["kind"] == "Secret":
                        self.assertLess(catalog_index, index)
                        self.assertLess(index, client_index)
                if product == "librechat":
                    model_index = names.index("librechat-agentgateway-model-values")
                    self.assertLess(client_index, model_index)
                    self.assertLess(model_index, names.index("librechat-product-values"))


class SyntheticClientCatalogIntegrationTests(unittest.TestCase):
    def test_public_models_and_oidc_roles_match(self) -> None:
        selected = catalog()
        selected["models"] += catalog("remote/second", "example/second")["models"]
        models = [
            yaml.safe_load(doc)
            for doc in resources(
                render("agentgateway", agent_values(selected)), "AgentgatewayModel"
            )
        ]
        names = {row["name"] for row in selected["models"]}
        self.assertEqual(
            {
                doc["spec"]["match"]["model"]
                for doc in models
                if doc["spec"]["visibility"] == "Public"
            },
            names,
        )
        self.assertEqual(len({doc["metadata"]["name"] for doc in models}), len(models))
        oidc = render(
            "keycloak/oidc/agentgateway",
            {
                "openrouterCatalog": selected,
                "authKeycloak": {"agentgatewayClientRoles": ["llm:invoke"]},
            },
        )
        self.assertEqual(
            set(json.loads(env_value(oidc, "KC_CLIENT_ROLES"))),
            {"llm:invoke", *(f"model:{name}:invoke" for name in names)},
        )


if __name__ == "__main__":
    unittest.main()
