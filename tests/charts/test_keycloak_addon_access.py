"""Render the optional, per-addon Keycloak ownership boundary."""

import json
import unittest

from helm import documents, env_value, render, resource


CHART = "keycloak/addon-access"
ADDON = {
    "enabled": True,
    "name": "forgejo",
    "realmRoles": ["forgejo-user", "forgejo-admin"],
    "realmRoleComposites": {"forgejo-admin": ["forgejo-user"]},
    "accessGroups": {"/access/neurwerk-forgejo-admins": {"realmRoles": ["forgejo-admin"]}},
    "platformAdminRole": "forgejo-admin",
    "platformAdminGrant": True,
}
VALUES = {"authKeycloak": {"realm": "example"}, "addonAccess": ADDON}


class KeycloakAddonAccessTests(unittest.TestCase):
    def test_unselected_chart_renders_nothing(self):
        self.assertEqual(documents(render(CHART, value_files=())), [])

    def test_selected_addon_scopes_roles_groups_and_egress(self):
        result = render(CHART, VALUES, value_files=())
        job = resource(result, "Job", "auth-keycloak-forgejo-access-job")
        egress = resource(result, "NetworkPolicy", "auth-keycloak-forgejo-access-egress")
        self.assertEqual(egress["spec"]["podSelector"]["matchLabels"], {
            "app": job["spec"]["template"]["metadata"]["labels"]["app"],
        })
        self.assertEqual(egress["metadata"]["namespace"], "auth-keycloak")
        self.assertEqual(json.loads(env_value(result, "KC_REALM_ROLE_COMPOSITES")), {
            "forgejo-admin": ["forgejo-user"], "platform-admin": ["forgejo-admin"],
        })
        self.assertEqual(json.loads(env_value(result, "KC_REALM_ROLE_COMPOSITE_OWNERSHIP")), {
            "forgejo-admin": ["forgejo-user", "forgejo-admin"],
            "platform-admin": ["forgejo-admin"],
        })
        self.assertEqual(json.loads(env_value(result, "KC_ACCESS_GROUPS")), ADDON["accessGroups"])
        without_grant = render(CHART, {**VALUES, "addonAccess": {
            **ADDON, "platformAdminGrant": False,
        }}, value_files=())
        self.assertEqual(json.loads(env_value(without_grant, "KC_REALM_ROLE_COMPOSITES"))["platform-admin"], [])

    def test_rejects_old_image_and_foreign_ownership(self):
        for change in (
            {"realmRoles": ["forgejo-admin", "studio-user"]},
            {"platformAdminRole": "keycloak-admin"},
            {"accessGroups": {"/access/neurwerk-platform-admins": {"realmRoles": ["forgejo-admin"]}}},
            {"realmRoleComposites": {"forgejo-admin": ["studio-user"]}},
        ):
            with self.subTest(change=change):
                result = render(CHART, {**VALUES, "addonAccess": {**ADDON, **change}},
                                check=False, value_files=())
                self.assertNotEqual(result.returncode, 0)
        old = render(CHART, {**VALUES, "k8sTools": {
            "image": "ghcr.io/neurwerk/k8s-stack-tooling:0.7.2",
        }}, check=False, value_files=())
        self.assertNotEqual(old.returncode, 0)
        self.assertIn("verified ownership-capable k8sTools.image pin", old.stderr)


if __name__ == "__main__":
    unittest.main()
