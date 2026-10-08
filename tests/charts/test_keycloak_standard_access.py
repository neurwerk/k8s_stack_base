"""Rendered ownership and separation contracts for standard Keycloak groups."""

from __future__ import annotations

import json
import unittest

from catalog import catalog
from helm import env_value, render

CHART = "keycloak/realm-config/realm-roles"
LLM = "/access/neurwerk-llm-all-users"
MCP = "/access/neurwerk-mcp-all-users"


class KeycloakStandardAccessTests(unittest.TestCase):
    def test_canonical_groups_roles_composites_and_fresh_admin(self) -> None:
        rendered = render(CHART)
        groups = json.loads(env_value(rendered, "KC_ACCESS_GROUPS"))
        role_names = (
            "keycloak-admin,api-key-admin,opensearch-admin,langfuse-admin,pii-admin,mcp-admin,"
            "platform-admin,studio-user,librechat-user,librechat-admin"
        )
        expected = {
            f"/access/neurwerk-{role}s": {"realmRoles": [role]}
            for role in role_names.split(",")
        }
        expected.update({LLM: {"realmRoles": []}, MCP: {"realmRoles": []}})
        for group in expected.values():
            group["clientRoles"] = {"agentgateway": []}
        self.assertEqual(groups, expected)
        self.assertEqual(env_value(rendered, "KC_REALM_ROLES"), role_names)
        self.assertEqual(json.loads(env_value(rendered, "KC_REALM_ROLE_COMPOSITES")), {
            "librechat-admin": ["librechat-user"],
            "platform-admin": [
                "keycloak-admin", "api-key-admin", "opensearch-admin",
                "langfuse-admin", "pii-admin", "mcp-admin", "studio-user", "librechat-admin",
            ],
        })
        self.assertEqual(json.loads(env_value(rendered, "KC_REALM_ROLE_COMPOSITE_OWNERSHIP")), {
            "librechat-admin": ["librechat-user"],
            "platform-admin": [
                "keycloak-admin", "api-key-admin", "opensearch-admin",
                "langfuse-admin", "pii-admin", "mcp-admin", "studio-user", "librechat-admin",
            ],
        })
        self.assertEqual(env_value(rendered, "KC_PARENT_ROLE"), "keycloak-admin")
        self.assertEqual(env_value(rendered, "KC_CLIENT_ID"), "realm-management")
        self.assertEqual(
            env_value(rendered, "KC_CLIENT_ROLES"),
            "view-users,query-users,view-clients,view-realm,view-events",
        )
        admin = render("keycloak/realm-config/initial-admin", {})
        memberships = json.loads(env_value(admin, "KC_INITIAL_USER_GROUPS"))
        self.assertEqual(memberships, ["/access/neurwerk-platform-admins"])
        self.assertTrue(all(group in groups for group in memberships))

    def test_platform_admin_exclusions_only_filter_direct_grants(self) -> None:
        baseline = render(CHART)
        composites = json.loads(env_value(baseline, "KC_REALM_ROLE_COMPOSITES"))
        defaults = composites["platform-admin"]
        for exclusions in (["studio-user", "librechat-admin"], defaults, []):
            with self.subTest(exclusions=exclusions):
                rendered = render(CHART, {"authKeycloak": {"platformAdminRoleExclusions": exclusions}})
                self.assertEqual(
                    json.loads(env_value(rendered, "KC_REALM_ROLE_COMPOSITES")),
                    {**composites, "platform-admin": [
                        role for role in defaults if role not in exclusions
                    ]},
                )
                self.assertEqual(
                    env_value(rendered, "KC_REALM_ROLE_COMPOSITE_OWNERSHIP"),
                    env_value(baseline, "KC_REALM_ROLE_COMPOSITE_OWNERSHIP"),
                )
                for name in ("KC_REALM_ROLES", "KC_ACCESS_GROUPS", "KC_PARENT_ROLE",
                             "KC_CLIENT_ID", "KC_CLIENT_ROLES"):
                    self.assertEqual(env_value(rendered, name), env_value(baseline, name))

    def test_legacy_single_slot_is_rejected_by_base_consumers(self) -> None:
        for chart in (CHART, "keycloak/server", "keycloak/realm-config/active-directory"):
            for legacy in ({}, {"realmRoles": ["catalog-admin"]}):
                with self.subTest(chart=chart, legacy=legacy):
                    result = render(chart, {"addonApplicationAccess": legacy}, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("addonApplicationAccess is no longer supported", result.stderr)

    def test_invalid_platform_admin_exclusions_fail_closed(self) -> None:
        for value, message in [
            *[(value, "must be a list") for value in (
                "", "librechat-admin", {}, {"librechat-admin": True}, False, True, 1, None,
            )],
            *[(value, "entries must be strings") for value in (
                [None], [False], [1], [{}], [[]],
            )],
            (["librechat-admin", "librechat-admin"], "duplicate role"),
            *[([role], "unknown direct application grant") for role in (
                "", "unknown-admin", "platform-admin", "dify-admin", "dify-user",
                "librechat-user", "/access/neurwerk-platform-admins",
                "llm:invoke", "model:example:invoke", "mcp:example:invoke",
            )],
        ]:
            with self.subTest(value=value):
                result = render(CHART, {
                    "authKeycloak": {"platformAdminRoleExclusions": value},
                }, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("authKeycloak.platformAdminRoleExclusions", result.stderr)
                self.assertIn(message, result.stderr)

    def test_client_resource_grants_do_not_change_application_groups(self) -> None:
        selected = catalog()
        del selected["grantToAccessGroups"]  # Exercise the fail-closed chart default.
        model = "model:remote/openrouter/acme/model:invoke"
        mcp = "mcp:example:invoke"
        values = {
            "openrouterCatalog": selected,
            "authKeycloak": {
                "platformAdminRoleExclusions": ["librechat-admin"],
                "agentgatewayClientRoles": ["llm:invoke", mcp],
                "agentgatewayAccessGroups": {
                    LLM: ["llm:invoke", model, model],
                    MCP: ["llm:invoke", mcp],
                },
            },
        }
        groups = json.loads(env_value(render(CHART, values), "KC_ACCESS_GROUPS"))
        self.assertEqual(groups.pop(LLM), {
            "realmRoles": [], "clientRoles": {"agentgateway": ["llm:invoke", model]},
        })
        self.assertEqual(groups.pop(MCP), {
            "realmRoles": [], "clientRoles": {"agentgateway": ["llm:invoke", mcp]},
        })
        self.assertEqual(len(groups), 10)
        self.assertTrue(all(
            group["clientRoles"] == {"agentgateway": []} for group in groups.values()
        ))

        for removed in (LLM, MCP):
            with self.subTest(removed=removed):
                del values["authKeycloak"]["agentgatewayAccessGroups"][removed]
                updated = json.loads(env_value(render(CHART, values), "KC_ACCESS_GROUPS"))
                self.assertEqual(updated[removed]["clientRoles"], {"agentgateway": []})
                for name, group in groups.items():
                    self.assertEqual(updated[name], group)
        self.assertTrue(all(
            group["clientRoles"] == {"agentgateway": []} for group in updated.values()
        ))

    def test_mapping_overrides_and_unsafe_grants_fail_closed(self) -> None:
        model = "model:example:invoke"
        mcp = "mcp:example:invoke"
        for auth, catalog_values, message in [
            ({"accessGroups": {}}, {}, "accessGroups is platform-owned"),
            ({"accessGroups": {LLM: {"realmRoles": ["platform-admin"]}}}, {}, "accessGroups is platform-owned"),
            ({"realmRoles": "platform-admin"}, {}, "realmRoles is platform-owned"),
            ({"realmRoleComposites": {}}, {}, "realmRoleComposites is platform-owned"),
            ({}, {"grantToAccessGroups": True}, "grantToAccessGroups must be false"),
            ({"agentgatewayAccessGroups": {"/access/neurwerk-platform-admins": [model]}}, {}, "only allowed for the two standard resource groups"),
            ({"agentgatewayAccessGroups": {"/access/neurwerk-studio-users": ["llm:invoke"]}}, {}, "only allowed for the two standard resource groups"),
            ({"agentgatewayAccessGroups": {"/access/custom": []}}, {}, "only allowed for the two standard resource groups"),
            ({"agentgatewayAccessGroups": {LLM: [mcp]}}, {}, "cannot grant cross-resource role"),
            ({"agentgatewayAccessGroups": {MCP: [model]}}, {}, "cannot grant cross-resource role"),
            ({"agentgatewayAccessGroups": {LLM: ["model:missing:invoke"]}}, {}, "grants undeclared role"),
            ({"agentgatewayAccessGroups": {LLM: "llm:invoke"}}, {}, "roles must be a list"),
        ]:
            with self.subTest(auth=auth, catalog=catalog_values):
                result = render(CHART, {
                    "authKeycloak": {
                        "agentgatewayClientRoles": ["llm:invoke", model, mcp], **auth,
                    },
                    "openrouterCatalog": catalog_values,
                }, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
