"""Rendered ownership and separation contracts for standard Keycloak groups."""

from __future__ import annotations

import json
import unittest

from catalog import catalog
from helm import env_value, render, resource

CHART = "keycloak/realm-config/realm-roles"
LLM = "/access/neurwerk-llm-all-users"
MCP = "/access/neurwerk-mcp-all-users"


class KeycloakStandardAccessTests(unittest.TestCase):
    def test_canonical_groups_roles_composites_and_fresh_admin(self) -> None:
        rendered = render(CHART)
        groups = json.loads(env_value(rendered, "KC_ACCESS_GROUPS"))
        role_names = (
            "keycloak-admin,api-key-admin,opensearch-admin,langfuse-admin,pii-admin,"
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
                "langfuse-admin", "pii-admin", "studio-user", "librechat-admin",
            ],
        })
        self.assertEqual(json.loads(env_value(rendered, "KC_REALM_ROLE_COMPOSITE_OWNERSHIP")), {
            "platform-admin": [
                "keycloak-admin", "api-key-admin", "opensearch-admin", "langfuse-admin",
                "pii-admin", "studio-user", "librechat-admin",
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
                for name in ("KC_REALM_ROLES", "KC_ACCESS_GROUPS", "KC_PARENT_ROLE",
                             "KC_CLIENT_ID", "KC_CLIENT_ROLES"):
                    self.assertEqual(env_value(rendered, name), env_value(baseline, name))

    def test_selected_application_access_is_bounded_and_keeps_base_grants(self) -> None:
        addon = {
            "enabled": True, "name": "forgejo", "realmRoles": ["forgejo-user", "forgejo-admin"],
            "platformAdminRole": "forgejo-admin", "platformAdminGrant": True,
            "realmRoleComposites": {"forgejo-admin": ["forgejo-user"]},
            "accessGroups": {"/access/neurwerk-forgejo-admins": {"realmRoles": ["forgejo-admin"]}},
        }
        values = {"addonAccess": addon, "authKeycloak": {"realm": "example"},
                  "k8sTools": {"image": "example.invalid/verified-tooling:reviewed"}}
        result = render("keycloak/addon-access", values, value_files=())
        job = resource(result, "Job", "auth-keycloak-forgejo-access-job")
        pod_labels = job["spec"]["template"]["metadata"]["labels"]
        egress = resource(result, "NetworkPolicy", "auth-keycloak-forgejo-access-egress")
        ingress = resource(render("keycloak/server", namespace="auth-keycloak"),
                           "NetworkPolicy", "auth-keycloak-keycloak-ingress")
        self.assertEqual(egress["spec"]["podSelector"]["matchLabels"], {"app": pod_labels["app"]})
        self.assertEqual(egress["metadata"]["namespace"], "auth-keycloak")
        self.assertEqual(ingress["metadata"]["namespace"], "auth-keycloak")
        self.assertEqual(ingress["spec"]["podSelector"]["matchLabels"],
                         egress["spec"]["egress"][1]["to"][0]["podSelector"]["matchLabels"])
        self.assertEqual(pod_labels["app.kubernetes.io/component"], "configuration")
        self.assertEqual(ingress["spec"]["ingress"][1]["from"][0]["podSelector"]["matchLabels"],
                         {"app.kubernetes.io/component": pod_labels["app.kubernetes.io/component"]})
        self.assertEqual(egress["spec"]["egress"][1]["ports"],
                         ingress["spec"]["ingress"][1]["ports"])
        composites = json.loads(env_value(result, "KC_REALM_ROLE_COMPOSITES"))
        self.assertEqual(composites, {"forgejo-admin": ["forgejo-user"],
                                      "platform-admin": ["forgejo-admin"]})
        self.assertEqual(json.loads(env_value(result, "KC_REALM_ROLE_COMPOSITE_OWNERSHIP")), {
            "forgejo-admin": ["forgejo-user", "forgejo-admin"],
            "platform-admin": ["forgejo-admin"],
        })
        groups = json.loads(env_value(result, "KC_ACCESS_GROUPS"))
        self.assertEqual(groups, addon["accessGroups"])
        disabled = render("keycloak/addon-access", {**values, "addonAccess": {
            **addon, "platformAdminGrant": False,
        }}, value_files=())
        self.assertEqual(json.loads(env_value(disabled, "KC_REALM_ROLE_COMPOSITES"))["platform-admin"], [])
        for bad in (
            {**addon, "realmRoles": ["forgejo-admin", "studio-user"]},
            {**addon, "platformAdminRole": "keycloak-admin"},
            {**addon, "accessGroups": {"/access/neurwerk-platform-admins": {"realmRoles": ["forgejo-admin"]}}},
            {**addon, "realmRoleComposites": {"forgejo-admin": ["studio-user"]}},
        ):
            self.assertNotEqual(render("keycloak/addon-access", {**values, "addonAccess": bad}, check=False, value_files=()).returncode, 0)
        self.assertNotEqual(render(CHART, {"addonApplicationAccess": addon}, check=False).returncode, 0)

    def test_invalid_platform_admin_exclusions_fail_closed(self) -> None:
        for value, message in [
            *[(value, "must be a list") for value in (
                "", "studio-user", {}, {"studio-user": True}, False, True, 1, None,
            )],
            *[(value, "entries must be strings") for value in (
                [None], [False], [1], [{}], [[]],
            )],
            (["studio-user", "studio-user"], "duplicate role"),
            *[([role], "unknown direct application grant") for role in (
                "", "unknown-admin", "platform-admin", "api-key-user",
                "librechat-user", "/access/neurwerk-platform-admins", "neurwerk-studio-users",
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
                "platformAdminRoleExclusions": ["studio-user"],
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
        self.assertEqual(len(groups), 9)
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
