"""Pinned native API setup; only verified, non-secret output reaches Studio."""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import secrets
import ssl
import sys
import time
from urllib.parse import quote

import httpx
import psycopg
from psycopg.rows import dict_row

from registrations import SetupError, reconcile, require

MARKER = "neurwerk-contextforge/setup-v1"
INVOKE = ["gateways.read", "servers.read", "servers.use", "tools.execute", "tools.read"]
PROVISION = ["admin.user_management", "teams.manage_members", "teams.read"]
PUBLISH = ["gateways.read", "servers.create", "servers.read", "servers.update", "tools.read"]


class API:
    def __init__(self, origin, headers, ca, deadline=None):
        self.origin = origin
        self.headers = headers
        self.deadline = deadline
        self.client = httpx.Client(verify=ssl.create_default_context(cafile=ca),
                                   timeout=120, trust_env=False, follow_redirects=False)

    def request(self, method, path, body=None, allow=()):
        if self.deadline is not None:
            remaining = self.deadline - time.monotonic()
            require(remaining > 0, "Native setup time budget exhausted")
            timeout = min(120, remaining)
        else:
            timeout = 120
        # A fresh request deliberately excludes cookies and ambient credentials.
        response = self.client.send(httpx.Request(method, self.origin + path,
                                                  headers=self.headers, json=body,
                                                  extensions={"timeout": dict.fromkeys(("connect", "read", "write", "pool"), timeout)}))
        if response.status_code in allow:
            return None
        require(200 <= response.status_code < 300,
                f"API {method} /{path.split('/')[1]} failed with status {response.status_code}; response hidden")
        return response.json() if response.content else None


class Database:
    """Read dormant ownership/grants that the native APIs omit; never write SQL."""

    def __init__(self):
        url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
        self.connection = psycopg.connect(url, row_factory=dict_row, connect_timeout=20)
        self.connection.read_only = True

    def rows(self, sql, **params):
        with self.connection.transaction():
            return self.connection.execute(sql, params).fetchall()


def exact(row, expected, message):
    require(isinstance(row, dict) and all(row.get(k) == v for k, v in expected.items()), message)


def role(api, db, name, scope, permissions, admin, previous=None, description=MARKER):
    rows = db.rows("SELECT id FROM roles WHERE name = %(name)s AND scope = %(scope)s",
                   name=name, scope=scope)
    require(len(rows) <= 1, "Duplicate setup role; operator repair required")
    require(not previous or (rows and rows[0]["id"] == previous), "Previously published role disappeared or changed")
    if rows:
        result = api.request("GET", "/rbac/roles/" + rows[0]["id"])
    else:
        result = api.request("POST", "/rbac/roles", {
            "name": name, "scope": scope, "description": description,
            "permissions": permissions, "inherits_from": None, "is_system_role": False,
        })
    exact(result, {"name": name, "scope": scope, "description": description,
                   "created_by": admin, "is_active": True, "inherits_from": None,
                   "is_system_role": False}, "Conflicting setup role; refusing to overwrite")
    # Role permissions are chart-owned. Reconcile only after proving ownership.
    if sorted(result["permissions"]) != permissions:
        api.request("PUT", "/rbac/roles/" + result["id"], {"permissions": permissions})
        result = api.request("GET", "/rbac/roles/" + result["id"])
    require(sorted(result["permissions"]) == permissions, "Conflicting setup role permissions")
    return result["id"]


def accounts(api, db, config, admin, previous):
    # Check the real administrator: proxy auth alone does not verify this state.
    user_path = "/auth/email/admin/users/"
    exact(api.request("GET", user_path + quote(admin, safe="")),
          {"email": admin, "is_admin": True, "is_active": True, "email_verified": True},
          "Registration administrator must be active and verified")
    service = config["serviceAccountEmail"]
    require(service != admin, "Studio service identity must differ from administrator")
    require(not previous.get("service_account_email") or previous["service_account_email"] == service,
            "Previously published Studio service identity changed")
    global_id = role(api, db, config["globalRoleName"], "global", [], admin, previous.get("global_role_id"))
    team_role_id = role(api, db, config["teamRoleName"], "team", INVOKE, admin, previous.get("team_role_id"))
    owner_id = role(api, db, config["ownerRoleName"], "team", sorted(PROVISION + PUBLISH),
                    admin, previous.get("owner_role_id"))
    # Include inactive teams: native POST otherwise silently reactivates them.
    teams = db.rows("SELECT id FROM email_teams WHERE name = %(name)s OR slug = %(name)s",
                    name=config["teamName"])
    require(len(teams) <= 1, "Conflicting fixed team")
    require(not previous.get("team_id") or (teams and teams[0]["id"] == previous["team_id"]),
            "Previously published team disappeared or changed")
    if teams:
        team = api.request("GET", "/teams/" + teams[0]["id"])
    else:
        team = api.request("POST", "/teams/", {"name": config["teamName"],
                           "description": MARKER, "visibility": "private"})
    exact(team, {"name": config["teamName"], "description": MARKER, "created_by": admin,
                 "visibility": "private", "is_personal": False, "is_active": True},
          "Conflicting fixed team; refusing to adopt")
    team_id = team["id"]
    path = user_path + quote(service, safe="")
    user = api.request("GET", path, allow=(404,))
    created = user is None
    if created:
        user = api.request("POST", user_path.rstrip("/"), {
            "email": service, "full_name": MARKER, "password": "Aa1!" + secrets.token_urlsafe(48),
            "is_admin": False, "is_active": False, "password_change_required": False,
        })
    exact(user, {"email": service, "full_name": MARKER, "is_admin": False,
                 "email_verified": True, "is_active": not created},
          "Conflicting or inactive Studio service account; operator repair required")
    expected = {(global_id, "global", None), (owner_id, "team", team_id)}

    def assignments():
        rows = db.rows("SELECT role_id, scope, scope_id, is_active, expires_at FROM user_roles "
                       "WHERE user_email = %(email)s", email=service)
        require(all(r["is_active"] and r["expires_at"] is None for r in rows),
                "Dormant or expiring Studio service grants; refusing to restore access")
        actual = {(r["role_id"], r["scope"], r["scope_id"]) for r in rows}
        require(len(actual) == len(rows) and actual <= expected, "Unexpected Studio service grants")
        return actual

    found = assignments()
    members = db.rows("SELECT team_id, role, is_active FROM email_team_members "
                      "WHERE user_email = %(email)s", email=service)
    if created:
        require(not members, "New Studio service account already has membership")
        api.request("POST", f"/teams/{team_id}/members", {"email": service, "role": "owner"})
        for rid, scope, scope_id in expected - assignments():
            api.request("POST", f"/rbac/users/{quote(service, safe='')}/roles",
                        {"role_id": rid, "scope": scope, "scope_id": scope_id})
    else:
        require(found == expected, "Missing Studio service grant; refusing to restore access")
    members = db.rows("SELECT team_id, role, is_active FROM email_team_members "
                      "WHERE user_email = %(email)s", email=service)
    require(members == [{"team_id": team_id, "role": "owner", "is_active": True}],
            "Conflicting Studio service membership")
    require(assignments() == expected, "Studio service grants not confirmed")
    if created:
        exact(api.request("PATCH", path, {"is_active": True}), {"is_active": True},
              "Studio service activation not confirmed")
    return {"team_id": team_id, "global_role_id": global_id, "team_role_id": team_role_id,
            "owner_role_id": owner_id, "service_account_email": service}


def admin_discovery_role(api, db, config, admin, previous):
    """Create only the scoped role; Studio leases it to the verified calling admin."""
    if not config.get("adminDiscovery", {}).get("enabled", False):
        return {}
    # Do not silently leave a former named operator's permanent native grants behind.
    require(not previous.get("operator_role_id"),
            "Retire the legacy named operator role and projection before enabling admin discovery")
    rid = role(api, db, "contextforge-tool-discovery", "team", ["gateways.update"], admin,
               previous.get("admin_discovery_role_id"), MARKER + "/admin-discovery")
    return {"admin_discovery_role_id": rid, "admin_discovery_ready": "true"}


def installed_projection(studio, results):
    """Hand off installed destinations only; Studio owns publication in PostgreSQL."""
    by_id = {entry["id"]: entry for entry in studio}
    require(len(by_id) == len(studio), "Duplicate Studio integration identity")
    entries, mappings, statuses = [], [], []
    for result in results:
        require(result["id"] in by_id, "Studio and registration projections disagree")
        entry = dict(by_id[result["id"]], gateway_id="", tool_names={})
        if result["state"] != "error":
            require(entry["server_id"] == result["server_id"], "Server projection conflict")
            entry["gateway_id"] = result["gateway_id"]
            mappings.append(result)
        # A failed installation remains visible without an unverified native ID.
        entries.append(entry)
        statuses.append({key: result[key] for key in ("id", "state", "error_code")})
    require(len(entries) == len(studio), "Studio and registration projections disagree")
    return entries, mappings, statuses


def main():
    # Conservative freshness: never claim verification began after a concurrent
    # Discover completed while this run was already checking native state.
    verification_started_at = datetime.now(timezone.utc).isoformat()
    config = json.loads(Path("/setup/config.json").read_text())
    admin = os.environ["PLATFORM_ADMIN_EMAIL"]
    # Reserve a minute to publish after slow providers; no per-provider Jobs needed.
    api = API(config["origin"], {"x-contextforge-account-email": admin}, "/trust/ca.crt",
              time.monotonic() + config["nativeBudgetSeconds"])
    sa = Path("/var/run/secrets/kubernetes.io/serviceaccount")
    kube = API("https://kubernetes.default.svc", {"Authorization": "Bearer " + (sa / "token").read_text()},
               str(sa / "ca.crt"))
    source_path = "/api/v1/namespaces/infra-agentgateway/configmaps/infra-agentgateway-mcp-catalog"
    source = kube.request("GET", source_path)
    require(source["data"]["catalogHash"] == config["catalogHash"], "Waiting for the matching chart catalog")
    target = "/api/v1/namespaces/frontend-studio/configmaps/contextforge-setup"
    output = kube.request("GET", target)
    require(output["metadata"]["labels"].get("app.kubernetes.io/part-of") == "contextforge",
            "Output ConfigMap ownership conflict")
    registrations = json.loads(source["data"]["registrations.json"])
    studio = json.loads(source["data"]["studio.json"])
    require(config.get("studioSetup") is True and source["data"].get("studioSetup") == "true",
            "Waiting for the matching MCP setup ownership mode")
    require(not output.get("data", {}).get("operator_role_id"),
            "Unresolved legacy operator role; explicit access retirement required before setup")
    require(not output.get("data") or output["data"].get("setup_mode") == "studio-v1",
            "Chart-owned installation requires a reviewed in-place migration before Studio setup")
    require(isinstance(registrations, list) and len(registrations) <= 200,
            "Catalog must contain at most 200 native registrations")
    previous_mappings = {r["id"]: r for r in json.loads(output.get("data", {}).get("mappings.json", "[]"))}
    db = Database()
    require(not db.rows("SELECT ur.role_id FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
                        "WHERE r.description LIKE %(marker)s", marker=MARKER + "/operator/%"),
            "Legacy operator grants remain; explicit access retirement required before setup")
    print("Verifying native team, roles and Studio service identity", flush=True)
    ids = accounts(api, db, config, admin, output.get("data", {}))
    if output.get("data", {}).get("admin_discovery_role_id"):
        ids["admin_discovery_role_id"] = output["data"]["admin_discovery_role_id"]
    # Failed admission never publishes administrator discovery readiness.
    try:
        ids.update(admin_discovery_role(api, db, config, admin, output.get("data", {})))
    except Exception:
        print("Administrator discovery unavailable; role needs explicit operator repair", flush=True)
    print("Reconciling approved native registrations", flush=True)
    service_api = API(config["origin"], {"x-contextforge-account-email": config["serviceAccountEmail"]},
                      "/trust/ca.crt", api.deadline)
    results = reconcile(api, registrations, ids["team_id"], admin, previous_mappings,
                        studio=(service_api, config["serviceAccountEmail"]))
    ids["setup_mode"] = "studio-v1"
    studio, mappings, statuses = installed_projection(studio, results)
    # Do not publish results for a catalog changed while this Job was running.
    require(kube.request("GET", source_path)["data"] == source["data"], "Catalog changed; retry setup")
    output["data"] = ids | {"studio.json": json.dumps(studio), "mappings.json": json.dumps(mappings),
                            "ready": "true", "catalogResourceVersion": source["metadata"]["resourceVersion"],
                            "catalog_hash": config["catalogHash"]}
    # In Studio mode this envelope verifies installation, not user selections.
    output["data"]["publication.json"] = json.dumps({"catalog_hash": config["catalogHash"],
        "checked_at": verification_started_at, "integrations": statuses})
    kube.request("PUT", target, output)
    print("Studio core configuration and verified native mappings published", flush=True)


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        main()
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # HTTP, database and validation exceptions may contain credentials/content.
        print(f"ContextForge setup failed ({type(exc).__name__}); details suppressed. Keep native activation blocked.", file=sys.stderr)
        sys.exit(1)
