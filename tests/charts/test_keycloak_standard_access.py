"""Rendered ownership and separation contracts for standard Keycloak groups."""

from __future__ import annotations

import json
import unittest

from test_openrouter_catalog import catalog, env_value, render


CHART = "keycloak/realm-config/realm-roles"
LLM = "/access/neurwerk-llm-all-users"
MCP = "/access/neurwerk-mcp-all-users"


class KeycloakStandardAccessTests(unittest.TestCase):
    def test_canonical_groups_roles_composites_and_fresh_admin(self) -> None:
        rendered = render(CHART, {"forgejo": {"enabled": False}})
        groups = json.loads(env_value(rendered, "KC_ACCESS_GROUPS"))
        role_names = (
            "keycloak-admin,api-key-admin,opensearch-admin,langfuse-admin,pii-admin,"
            "platform-admin,studio-user,librechat-user,librechat-admin,dify-user,dify-admin"
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
            "dify-admin": ["dify-user"],
            "platform-admin": [
                "keycloak-admin", "api-key-admin", "opensearch-admin",
                "langfuse-admin", "pii-admin", "studio-user", "librechat-admin", "dify-admin",
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

    def test_forgejo_selection_only_adds_owned_admission_definitions(self) -> None:
        disabled = render(CHART, {"forgejo": {"enabled": False}})
        # Null removes the synthetic fixture override, exercising the chart default.
        default = render(CHART, {"forgejo": {"enabled": None}})
        enabled = render(CHART, {"forgejo": {"enabled": True}})
        for name in ("KC_REALM_ROLES", "KC_REALM_ROLE_COMPOSITES", "KC_ACCESS_GROUPS"):
            self.assertEqual(env_value(default, name), env_value(disabled, name))
            self.assertNotIn("forgejo", env_value(disabled, name))
        self.assertEqual(env_value(enabled, "KC_REALM_ROLES"),
                         env_value(disabled, "KC_REALM_ROLES") + ",forgejo-user,forgejo-admin")
        composites = json.loads(env_value(disabled, "KC_REALM_ROLE_COMPOSITES"))
        composites["platform-admin"].append("forgejo-admin")
        self.assertEqual(json.loads(env_value(enabled, "KC_REALM_ROLE_COMPOSITES")),
                         {**composites, "forgejo-admin": ["forgejo-user"]})
        groups = json.loads(env_value(disabled, "KC_ACCESS_GROUPS"))
        self.assertEqual(len(groups), 13)
        groups.update({f"/access/neurwerk-{role}s": {
            "realmRoles": [role], "clientRoles": {"agentgateway": []},
        } for role in ("forgejo-user", "forgejo-admin")})
        self.assertEqual(len(groups), 15)
        self.assertEqual(json.loads(env_value(enabled, "KC_ACCESS_GROUPS")), groups)
        for selected in (False, True):
            result = render(CHART, {
                "forgejo": {"enabled": selected},
                "authKeycloak": {"realmRoles": "forgejo-admin"},
            }, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("realmRoles is platform-owned", result.stderr)

    def test_platform_admin_exclusions_only_filter_direct_grants(self) -> None:
        for enabled in (False, True):
            baseline = render(CHART, {"forgejo": {"enabled": enabled}})
            composites = json.loads(env_value(baseline, "KC_REALM_ROLE_COMPOSITES"))
            defaults = composites["platform-admin"]
            for exclusions in (
                ["forgejo-admin"], ["dify-admin", "studio-user", "librechat-admin"],
                defaults, [],
            ):
                with self.subTest(enabled=enabled, exclusions=exclusions):
                    rendered = render(CHART, {
                        "forgejo": {"enabled": enabled},
                        "authKeycloak": {"platformAdminRoleExclusions": exclusions},
                    })
                    self.assertEqual(
                        json.loads(env_value(rendered, "KC_REALM_ROLE_COMPOSITES")),
                        {**composites, "platform-admin": [
                            role for role in defaults if role not in exclusions
                        ]},
                    )
                    for name in ("KC_REALM_ROLES", "KC_ACCESS_GROUPS", "KC_PARENT_ROLE",
                                 "KC_CLIENT_ID", "KC_CLIENT_ROLES"):
                        self.assertEqual(env_value(rendered, name), env_value(baseline, name))

    def test_invalid_platform_admin_exclusions_fail_closed(self) -> None:
        for value, message in [
            *[(value, "must be a list") for value in (
                "", "dify-admin", {}, {"dify-admin": True}, False, True, 1, None,
            )],
            *[(value, "entries must be strings") for value in (
                [None], [False], [1], [{}], [[]],
            )],
            (["dify-admin", "dify-admin"], "duplicate role"),
            *[([role], "unknown direct application grant") for role in (
                "", "unknown-admin", "platform-admin", "forgejo-user", "dify-user",
                "librechat-user", "/access/neurwerk-platform-admins", "neurwerk-dify-admins",
                "llm:invoke", "model:example:invoke", "mcp:example:invoke",
            )],
        ]:
            for enabled in (False, True):
                with self.subTest(value=value, enabled=enabled):
                    result = render(CHART, {
                        "forgejo": {"enabled": enabled},
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
            "forgejo": {"enabled": True},
            "openrouterCatalog": selected,
            "authKeycloak": {
                "platformAdminRoleExclusions": ["forgejo-admin", "dify-admin"],
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
        self.assertEqual(len(groups), 13)
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
