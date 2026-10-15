"""Current catalog ownership and explicit authorization boundaries."""

import json
import unittest

import yaml

from catalog import catalog
from helm import ROOT, env_value, render


class AuthorizationCatalogTests(unittest.TestCase):
    def test_oidc_roles_are_derived_and_legacy_roles_are_deduplicated(self):
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

    def test_access_group_grants_are_explicit_and_catalog_roles_remain_available(self):
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
        self.assertEqual(explicit[group]["clientRoles"]["agentgateway"], ["llm:invoke"])
        values["authKeycloak"]["agentgatewayAccessGroups"][group].append(permission)
        granted = json.loads(env_value(render("keycloak/realm-config/realm-roles", values), "KC_ACCESS_GROUPS"))
        self.assertEqual(granted[group]["clientRoles"]["agentgateway"], ["llm:invoke", permission])
        values["openrouterCatalog"]["excludedModels"] = ["acme/model"]
        failed = render("keycloak/realm-config/realm-roles", values, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("grants undeclared role", failed.stderr)

    def test_access_group_environment_boundary(self):
        def boundary_values(over_limit):
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
                    "enabled": False, "excludedModels": [],
                    "grantToAccessGroups": False, "models": [],
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
        self.assertIn("exceeds the 120000-byte process environment safety limit", failed.stderr)


class CatalogOwnershipTests(unittest.TestCase):
    def test_client_values_override_optional_catalog(self):
        for path, product in (
            ("agentgateway/app.yaml", "agentgateway"),
            ("keycloak/oidc-agentgateway.yaml", "keycloak"),
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
                self.assertEqual(refs[catalog_index], {
                    "kind": "ConfigMap", "name": "client-openrouter-catalog-values",
                    "valuesKey": "values.yaml", "optional": True,
                })
                self.assertLess(catalog_index, client_index)
                self.assertLess(client_index, names.index(f"{product}-product-values"))
                for index, ref in enumerate(refs):
                    if ref["kind"] == "ConfigMap" and (
                        ref["name"].startswith("base-shared-") or ref["name"].endswith("-app-defaults")
                    ):
                        self.assertLess(index, catalog_index, ref["name"])
                    if ref["kind"] == "Secret":
                        self.assertLess(catalog_index, index)
                        if ref["name"] == "mcp-runtime-values":
                            self.assertEqual(product, "agentgateway")
                            self.assertGreater(index, names.index("agentgateway-product-values"))
                        else:
                            self.assertLess(index, client_index)
                if product == "librechat":
                    model_index = names.index("librechat-agentgateway-model-values")
                    self.assertLess(client_index, model_index)
                    self.assertLess(model_index, names.index("librechat-product-values"))
