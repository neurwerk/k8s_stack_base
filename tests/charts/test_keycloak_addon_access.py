"""Render the optional, per-addon Keycloak ownership boundary."""

import json
import unittest

from helm import documents, env_value, render, resource


CHART = "keycloak/addon-access"
ADDON = {
    "enabled": True,
    "name": "sample",
    "realmRoles": ["sample-addon-user", "sample-addon-admin"],
    "realmRoleComposites": {"sample-addon-admin": ["sample-addon-user"]},
    "accessGroups": {"/access/neurwerk-sample-addon-admins": {"realmRoles": ["sample-addon-admin"]}},
    "platformAdminRole": "sample-addon-admin",
    "platformAdminGrant": True,
}
VALUES = {"authKeycloak": {"realm": "example"}, "addonAccess": ADDON}


class KeycloakAddonAccessTests(unittest.TestCase):
    def test_unselected_chart_renders_nothing(self):
        self.assertEqual(documents(render(CHART, value_files=())), [])

    def test_selected_addon_scopes_roles_groups_and_egress(self):
        result = render(CHART, VALUES, value_files=())
        job = resource(result, "Job", "auth-keycloak-sample-access-job")
        egress = resource(result, "NetworkPolicy", "auth-keycloak-sample-access-egress")
        self.assertEqual(egress["spec"]["podSelector"]["matchLabels"], {
            "app": job["spec"]["template"]["metadata"]["labels"]["app"],
        })
        self.assertEqual(egress["metadata"]["namespace"], "auth-keycloak")
        self.assertEqual(json.loads(env_value(result, "KC_REALM_ROLE_COMPOSITES")), {
            "sample-addon-admin": ["sample-addon-user"], "platform-admin": ["sample-addon-admin"],
        })
        self.assertEqual(json.loads(env_value(result, "KC_REALM_ROLE_COMPOSITE_OWNERSHIP")), {
            "sample-addon-admin": ["sample-addon-user", "sample-addon-admin"],
            "platform-admin": ["sample-addon-admin"],
        })
        self.assertEqual(json.loads(env_value(result, "KC_ACCESS_GROUPS")), ADDON["accessGroups"])
        without_grant = render(CHART, {**VALUES, "addonAccess": {
            **ADDON, "platformAdminGrant": False,
        }}, value_files=())
        self.assertEqual(json.loads(env_value(without_grant, "KC_REALM_ROLE_COMPOSITES"))["platform-admin"], [])

    def test_rejects_old_image_and_foreign_ownership(self):
        for change in (
            {"realmRoles": ["sample-addon-admin", "studio-user"]},
            {"platformAdminRole": "keycloak-admin"},
            {"accessGroups": {"/access/neurwerk-platform-admins": {"realmRoles": ["sample-addon-admin"]}}},
            {"realmRoleComposites": {"sample-addon-admin": ["studio-user"]}},
        ):
            with self.subTest(change=change):
                result = render(CHART, {**VALUES, "addonAccess": {**ADDON, **change}},
                                check=False, value_files=())
                self.assertNotEqual(result.returncode, 0)
        old = render(CHART, {**VALUES, "k8sTools": {
            "image": "ghcr.io/neurwerk/k8s-stack-tooling:0.7.2",
        }}, check=False, value_files=())
        self.assertNotEqual(old.returncode, 0)
        self.assertIn("explicit immutable k8sTools.image pin", old.stderr)

    def test_independent_immutable_ownership_capable_pin(self):
        # Synthetic digests exercise selection, not publication evidence.
        for version, accepted in (("0.11.1", False), ("0.12.0", True), ("0.13.8", True)):
            with self.subTest(version=version):
                image = f"ghcr.io/neurwerk/k8s-stack-tooling:{version}@sha256:" + "0" * 64
                result = render(CHART, {**VALUES, "k8sTools": {"image": image}},
                                check=False, value_files=())
                self.assertEqual(result.returncode == 0, accepted)


if __name__ == "__main__":
    unittest.main()
