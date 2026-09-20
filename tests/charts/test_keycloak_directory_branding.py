"""Directory trust, mapping safety and mounted branding contracts."""

import json
import unittest

from helm import documents, env_value, render, resource

AD = {
    "enabled": True,
    "connectionUrl": "ldaps://ad.example:636",
    "usersDn": "OU=Users,DC=example",
    "groupsDn": "OU=Groups,DC=example",
    "groupNames": ["neurwerk-platform-admins"],
    "egressCidrs": ["192.0.2.1/32"],
}
MAPPING = {
    "sourceName": "APP_Users (West), R&D+Ops\\Team",
    "targetParent": "/access/neurwerk-studio-users",
}
MAPPED = {**AD, "groupNames": [], "groupMappings": [MAPPING]}
PLAIN = {
    **AD,
    "connectionUrl": "ldap://ad.example:389",
    "allowInsecureLdap": True,
    "connectionTimeoutMs": 2000,
    "readTimeoutMs": 3000,
    "caConfigMapName": "",
    "caKey": "",
}
BRAND = {"enabled": True, "logoConfigMapName": "keycloak-branding-logo"}


class KeycloakDirectoryTests(unittest.TestCase):
    def test_transport_and_job_inputs(self):
        for settings in (
            {**AD, "enabled": False},
            AD,
            MAPPED,
            PLAIN,
            {**PLAIN, "groupNames": [], "groupMappings": [MAPPING]},
        ):
            with self.subTest(settings=settings):
                enabled = settings["enabled"]
                plain = settings.get("allowInsecureLdap", False)
                values = {"authKeycloak": {"activeDirectory": settings, "smtp": {"enabled": False}}}
                server = render("keycloak/server", values, namespace="auth-keycloak")
                sts = resource(server, "StatefulSet")
                pod = sts["spec"]["template"]["spec"]
                container = pod["containers"][0]
                env = {item["name"]: item for item in container["env"]}
                volumes = {item["name"]: item for item in pod["volumes"]}
                mounts = {item["name"]: item for item in container["volumeMounts"]}
                secure = enabled and not plain
                self.assertEqual("KC_TRUSTSTORE_PATHS" in env, secure)
                self.assertEqual("active-directory-ca" in volumes, secure)
                self.assertEqual("active-directory-ca" in mounts, secure)
                if secure:
                    self.assertEqual(
                        volumes["active-directory-ca"]["configMap"]["name"],
                        "auth-keycloak-active-directory-ca",
                    )
                    self.assertTrue(mounts["active-directory-ca"]["readOnly"])
                self.assertIn("postgres-ca", volumes)
                self.assertIn("postgres-ca", mounts)
                self.assertIn("sslmode=verify-full", env["KC_DB_URL"]["value"])
                self.assertFalse(
                    {
                        "KC_TLS_HOSTNAME_VERIFIER",
                        "KC_SPI_TRUSTSTORE_FILE_HOSTNAME_VERIFICATION_POLICY",
                    }
                    & env.keys()
                )
                self.assertEqual(
                    sts["metadata"]
                    .get("annotations", {})
                    .get("configmap.reloader.stakater.com/reload", ""),
                    "auth-keycloak-active-directory-ca" if secure else "",
                )
                rules = resource(server, "NetworkPolicy", "auth-keycloak-keycloak-egress")["spec"][
                    "egress"
                ]
                self.assertEqual(
                    [rule for rule in rules if any("ipBlock" in peer for peer in rule["to"])],
                    (
                        [
                            {
                                "to": [{"ipBlock": {"cidr": "192.0.2.1/32"}}],
                                "ports": [{"port": 389 if plain else 636, "protocol": "TCP"}],
                            }
                        ]
                        if enabled
                        else []
                    ),
                )
                job = render("keycloak/realm-config/active-directory", values)
                self.assertEqual(
                    any(doc["kind"] == "ExternalSecret" for doc in documents(job)), enabled
                )
                env = {
                    item["name"]: item
                    for item in resource(job, "Job")["spec"]["template"]["spec"]["containers"][0][
                        "env"
                    ]
                }
                self.assertEqual(env["KC_ACTIVE_DIRECTORY_ENABLED"]["value"], str(enabled).lower())
                if not enabled:
                    self.assertEqual(
                        [name for name in env if name.startswith("KC_ACTIVE_DIRECTORY_")],
                        ["KC_ACTIVE_DIRECTORY_ENABLED"],
                    )
                    continue
                for suffix, expected in (
                    ("GROUP_NAMES", settings["groupNames"]),
                    ("GROUP_MAPPINGS", settings.get("groupMappings", [])),
                    ("CONNECTION_TIMEOUT_MS", settings.get("connectionTimeoutMs", 5000)),
                    ("READ_TIMEOUT_MS", settings.get("readTimeoutMs", 10000)),
                ):
                    self.assertEqual(
                        json.loads(env[f"KC_ACTIVE_DIRECTORY_{suffix}"]["value"]), expected
                    )
                self.assertEqual(
                    env["KC_ACTIVE_DIRECTORY_ALLOW_INSECURE_LDAP"]["value"], str(plain).lower()
                )
                for suffix, key in (
                    ("DN", "activeDirectoryBindDn"),
                    ("CREDENTIAL", "activeDirectoryBindCredential"),
                ):
                    self.assertEqual(
                        env[f"KC_ACTIVE_DIRECTORY_BIND_{suffix}"]["valueFrom"],
                        {
                            "secretKeyRef": {
                                "name": "auth-keycloak-active-directory-secret",
                                "key": key,
                            },
                        },
                    )

    def test_runtime_gate_for_mapping_and_plaintext(self):
        image = "ghcr.io/neurwerk/k8s-stack-tooling:"
        for chart in ("server", "realm-config/active-directory"):
            # Each feature must independently reject the old runtime.
            for settings in (MAPPED, PLAIN):
                with (
                    self.subTest(chart=chart, settings=settings),
                    self.assertRaisesRegex(AssertionError, ">=0.7.0"),
                ):
                    render(
                        f"keycloak/{chart}",
                        {
                            "authKeycloak": {"activeDirectory": settings},
                            "k8sTools": {"image": image + "0.6.2"},
                        },
                    )
            for tag, valid in (
                ("0.7.0", True),
                ("0.7.0@sha256:" + "a" * 64, True),
                ("0.10.0", True),
                ("1.0.0", True),
                ("0.6.2@sha256:" + "a" * 64, False),
                ("0.7", False),
                ("latest", False),
                ("0.7.0-rc.1", False),
                ("0.7.0@sha256:abc", False),
            ):
                with self.subTest(chart=chart, tag=tag):
                    result = render(
                        f"keycloak/{chart}",
                        {
                            "authKeycloak": {"activeDirectory": MAPPED},
                            "k8sTools": {"image": image + tag},
                        },
                        check=False,
                    )
                    self.assertEqual(result.returncode == 0, valid, result.stderr)
                    if not valid:
                        self.assertIn("requires k8sTools.image", result.stderr)
            with self.assertRaisesRegex(AssertionError, "requires k8sTools.image"):
                render(
                    f"keycloak/{chart}",
                    {
                        "authKeycloak": {"activeDirectory": MAPPED},
                        "k8sTools": {"image": "registry.example/tooling:0.7.0"},
                    },
                )

    def test_ambiguous_mapping_injection_and_unsafe_transport_fail(self):
        for chart in ("server", "realm-config/active-directory"):
            for override, error in (
                ({"groupNames": AD["groupNames"]}, "exactly one non-empty list"),
                ({"groupMappings": []}, "exactly one non-empty list"),
                ({"groupMappings": {}}, "must be lists"),
                (
                    {"groupMappings": [{**MAPPING, "extra": True}]},
                    "only sourceName and targetParent",
                ),
                (
                    {
                        "groupMappings": [
                            MAPPING,
                            {**MAPPING, "sourceName": MAPPING["sourceName"].lower()},
                        ]
                    },
                    "duplicate sourceName",
                ),
                (
                    {"groupMappings": [MAPPING, {**MAPPING, "sourceName": "Other"}]},
                    "duplicate targetParent",
                ),
                *[
                    ({"groupMappings": [{**MAPPING, "sourceName": name}]}, "sourceName")
                    for name in (
                        " AD_USERS",
                        "AD\nUSERS",
                        "AD\x00USERS",
                        "${GROUP}",
                        "{{group}}",
                        "<group>",
                        "REPLACE_ME",
                        "x" * 65,
                    )
                ],
                *[
                    ({"groupMappings": [{**MAPPING, "targetParent": target}]}, "canonical /access/")
                    for target in (
                        "/access/neurwerk-unknown",
                        "/access/neurwerk-studio-users/child",
                    )
                ],
                ({"allowInsecureLdap": "true"}, "must be a boolean"),
                *[
                    ({"connectionUrl": url}, "connectionUrl")
                    for url in (
                        "ldap://ad.example:389",
                        "ldaps://ad.example:389",
                        "ldaps://ad.example",
                        "ldaps://ad.example:636/path",
                    )
                ],
                (
                    {"connectionUrl": "ldap://ad.example:636", "allowInsecureLdap": True},
                    "connectionUrl",
                ),
                ({"caConfigMapName": ""}, "required for LDAPS"),
                ({"caKey": ""}, "required for LDAPS"),
            ):
                with (
                    self.subTest(chart=chart, override=override),
                    self.assertRaisesRegex(AssertionError, error),
                ):
                    render(
                        f"keycloak/{chart}",
                        {"authKeycloak": {"activeDirectory": {**MAPPED, **override}}},
                    )


class KeycloakBrandingTests(unittest.TestCase):
    def test_brand_assets_properties_and_reload(self):
        disabled = render("keycloak/server")
        for text in (
            "KC_REALM_LOGIN_THEME",
            "KC_REALM_EMAIL_THEME",
            "name: client-brand",
            "checksum/branding",
        ):
            self.assertNotIn(text, disabled.stdout)
        checksums = set()
        for fmt in ("png", "svg"):
            result = render(
                "keycloak/server",
                {
                    "authKeycloak": {
                        "branding": {**BRAND, "logoFormat": fmt},
                        "activeDirectory": AD,
                        "realmDisplayName": " Company =:#!\t",
                        "loginTheme": "client-brand",
                        "emailTheme": "client-brand",
                    }
                },
            )
            expected = "parent=neurwerk\ncompanyName=\\ Company\\ \\=\\:\\#\\!\\t\n"
            self.assertEqual(
                resource(result, "ConfigMap")["data"],
                {
                    "login-theme.properties": expected + f"companyLogoFormat={fmt}\n",
                    "email-theme.properties": expected,
                },
            )
            sts = resource(result, "StatefulSet")
            self.assertEqual(
                sts["metadata"]["annotations"]["configmap.reloader.stakater.com/reload"],
                "auth-keycloak-active-directory-ca,keycloak-branding-logo",
            )
            pod = sts["spec"]["template"]
            checksums.add(pod["metadata"]["annotations"]["checksum/branding"])
            volume = next(v for v in pod["spec"]["volumes"] if v["name"] == "client-brand")[
                "projected"
            ]
            self.assertEqual(volume["defaultMode"], 0o444)
            self.assertEqual(
                volume["sources"],
                [
                    {
                        "configMap": {
                            "name": "auth-keycloak-branding-properties",
                            "items": [
                                {
                                    "key": f"{kind}-theme.properties",
                                    "path": f"{kind}/theme.properties",
                                }
                                for kind in ("login", "email")
                            ],
                        }
                    },
                    {
                        "configMap": {
                            "name": BRAND["logoConfigMapName"],
                            "items": [
                                {
                                    "key": f"company-logo.{fmt}",
                                    "path": f"login/resources/img/company-logo.{fmt}",
                                }
                            ],
                        }
                    },
                ],
            )
            self.assertIn(
                {
                    "name": "client-brand",
                    "mountPath": "/opt/keycloak/themes/client-brand",
                    "readOnly": True,
                },
                pod["spec"]["containers"][0]["volumeMounts"],
            )
            for name in ("KC_REALM_LOGIN_THEME", "KC_REALM_EMAIL_THEME"):
                self.assertEqual(env_value(result, name), "client-brand")
        self.assertEqual(len(checksums), 2)
        renamed = render(
            "keycloak/server", {"authKeycloak": {"branding": BRAND, "realmDisplayName": "Renamed"}}
        )
        sts = resource(renamed, "StatefulSet")
        self.assertNotIn(
            sts["spec"]["template"]["metadata"]["annotations"]["checksum/branding"], checksums
        )
        self.assertEqual(
            sts["metadata"]["annotations"]["configmap.reloader.stakater.com/reload"],
            BRAND["logoConfigMapName"],
        )

    def test_invalid_branding_fails(self):
        for auth, error in (
            *[
                ({key: "client-brand"}, "client-brand selection requires")
                for key in ("loginTheme", "emailTheme")
            ],
            *[
                ({"branding": {**BRAND, "logoFormat": fmt}}, "logoFormat must be png or svg")
                for fmt in ("jpg", "../svg", True)
            ],
            *[
                (
                    {"branding": BRAND, "realmDisplayName": name},
                    "must not contain CR, LF, or property interpolation",
                )
                for name in ("line\nbreak", "line\rbreak", "${env.NAME}", "\\${name}")
            ],
            *[
                ({"branding": {**BRAND, "logoConfigMapName": name}}, "logoConfigMapName")
                for name in ("", "../logo", "logo,other")
            ],
        ):
            with self.subTest(auth=auth), self.assertRaisesRegex(AssertionError, error):
                render("keycloak/server", {"authKeycloak": auth})
