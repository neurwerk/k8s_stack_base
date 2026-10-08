"""Reconcile only chart-approved native registrations; never platform grants."""

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import hashlib
import json
import re


class SetupError(Exception):
    """Only explicit, credential-free messages may reach Job logs."""


class PendingDiscovery(SetupError):
    """An empty OAuth server still needs operator discovery."""


def config_hash(spec):
    # A resolved gateway ID is output, not a change to the approved definition.
    definition = {key: value for key, value in spec.items() if key != "gateway_id"}
    return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()


def matches_previous(spec, previous):
    return (previous.get("approved_config_hash") == config_hash(spec)
            and spec.get("gateway_id") in (None, previous.get("gateway_id"))
            and spec["server_id"] == previous.get("server_id"))


def require(ok, message):
    if not ok:
        raise SetupError(message)


def address(url, exact=False):
    p = urlsplit(url)
    require(p.scheme in {"http", "https"} and p.hostname and not p.username
            and not p.password and not p.query and not p.fragment, "Invalid registration address")
    host = p.hostname.lower()
    if p.port and (p.scheme, p.port) not in {("http", 80), ("https", 443)}:
        host += f":{p.port}"
    return urlunsplit((p.scheme, host, p.path if exact else p.path.rstrip("/"), "", ""))


def catalog(api, kind):
    rows = api.request("GET", f"/{kind}?include_inactive=true&limit=0")
    require(isinstance(rows, list), "Native catalog response is not a list")
    return rows


def empty(value):
    return value in (None, "", [], {})


def owned(row, spec, team, owner, description):
    expected = {"name": "neurwerk-contextforge-" + spec["id"], "description": description,
                "teamId": team, "ownerEmail": owner, "createdBy": owner,
                "visibility": spec["visibility"]}
    require(all(row.get(k) == v for k, v in expected.items()), "Registration ownership conflict")


def native_oauth(spec):
    return {k: v for k, v in spec["oauth"].items() if k not in {"client_secret_ref", "pkce"}} | {
        "grant_type": "authorization_code"}


def check_gateway(row, spec, team, owner, marker):
    owned(row, spec, team, owner, marker)
    require(re.fullmatch(r"[0-9a-f]{32}", row["id"]), "Invalid native gateway ID")
    require(not spec.get("gateway_id") or row["id"] == spec["gateway_id"], "Gateway ID conflict")
    require(row.get("enabled") is True and row.get("status") == "active"
            and row.get("gatewayMode") == "cache" and row.get("transport") == spec["transport"]
            and address(row["url"], True) == address(spec["upstream_url"], True),
            "Conflicting or inactive native gateway")
    forbidden = ["authValue", "authHeaders", "authHeadersUnmasked", "authUsername", "authPassword",
                 "authPasswordUnmasked", "authToken", "authTokenUnmasked", "authHeaderKey",
                 "authHeaderValue", "authHeaderValueUnmasked", "authQueryParamKey",
                 "authQueryParamValueMasked", "passthroughHeaders", "clientCert", "clientKey",
                 "identityPropagation"]
    require(all(empty(row.get(k)) for k in forbidden), "Unexpected native credential or identity injection")
    if spec.get("oauth"):
        expected = native_oauth(spec)
        actual = row.get("oauthConfig")
        require(row.get("authType") == "oauth" and isinstance(actual, dict)
                and set(actual) == set(expected) | {"client_secret"}
                and all(actual.get(k) == v for k, v in expected.items())
                and isinstance(actual.get("client_secret"), str) and actual["client_secret"],
                "Conflicting OAuth app metadata; refusing to rotate or overwrite")
    else:
        require(row.get("authType") in (None, "", "none") and empty(row.get("oauthConfig"))
                and row.get("reachable") is True, "Upstream authentication or reachability conflict")


def tool_id(row, gateway, spec, team, owner):
    require(row.get("gatewayId") == gateway["id"] and row.get("teamId") == team
            and row.get("ownerEmail") == owner and row.get("visibility") == spec["visibility"]
            and row.get("integrationType") == "MCP"
            and address(row["url"], True) == address(spec["upstream_url"], True),
            "Cross-integration tool association")
    require(all(empty(row.get(k)) for k in ("headers", "auth", "headerMapping", "pluginChainPre", "pluginChainPost")),
            "Unexpected tool credential, header or plugin")
    require(re.fullmatch(r"[0-9a-f]{32}", row["id"]), "Invalid native tool ID")
    return row["id"]


def members(api, server, gateway, spec, team, owner, marker):
    owned(server, spec, team, owner, marker + "/" + gateway["id"])
    require(server.get("id") == spec["server_id"] and server.get("enabled") is True
            and server.get("oauthEnabled") is False and empty(server.get("oauthConfig"))
            and all(server.get(k) == [] for k in ("associatedResources", "associatedPrompts", "associatedA2aAgents")),
            "Conflicting server state or non-tool associations")
    rows = api.request("GET", f"/servers/{spec['server_id']}/tools?include_inactive=true")
    ids = [tool_id(row, gateway, spec, team, owner) for row in rows]
    active = {row["id"] for row in rows if row.get("enabled") is True}
    require(len(ids) == len(set(ids)) and set(server["associatedToolIds"]) == active,
            "Native server membership views disagree")
    for kind in ("resources", "prompts"):
        require(api.request("GET", f"/servers/{spec['server_id']}/{kind}?include_inactive=true") == [],
                "Non-tool server associations are unsupported")
    return set(ids)


def approved(api, gateway, spec, team, owner):
    rows = api.request("GET", f"/tools?gateway_id={gateway['id']}&include_inactive=true&limit=0")
    ids = set()
    missing = False
    for name in spec["approved_tools"]:
        matches = [row for row in rows if row.get("originalName") == name]
        if not matches and spec.get("oauth"):
            missing = True
            continue
        require(len(matches) == 1 and matches[0].get("enabled") is True,
                "Approved tool missing, ambiguous or disabled")
        ids.add(tool_id(matches[0], gateway, spec, team, owner))
    if missing:
        raise PendingDiscovery("Approved OAuth tool not yet discovered")
    require(len(ids) == len(spec["approved_tools"]), "Duplicate approved tool IDs")
    return ids


def reconcile(api, specs, team, owner, previous=None):
    """Yield each provider for immediate projection before starting the next."""
    previous = previous or {}
    for values in ([s["id"] for s in specs], [s["server_id"] for s in specs],
                   [address(s["upstream_url"]) for s in specs]):
        require(len(values) == len(set(values)), "Duplicate registration identity or upstream")
    for spec in specs:
        try:
            old = previous.get(spec["id"], {})
            if old:
                require(spec["server_id"] == old["server_id"]
                        and spec.get("gateway_id") in (None, old["gateway_id"]),
                        "Previously published native mapping changed")
                # Alias verification still rejects a replacement gateway with the same name.
                spec = dict(spec, gateway_id=old["gateway_id"])
            result = reconcile_one(api, spec, team, owner)
        except Exception as exc:
            # No response bodies or exception messages may enter publication data.
            old = previous.get(spec["id"], {})
            result = {"id": spec["id"], "state": "error",
                      "error_code": "verification-failed" if isinstance(exc, SetupError) else "provider-unavailable"}
            if matches_previous(spec, old):
                result.update({key: old[key] for key in ("gateway_id", "server_id", "approved_config_hash")})
        yield result


def reconcile_one(api, spec, team, owner):
    alias = "neurwerk-contextforge-" + spec["id"]
    marker = f"neurwerk-contextforge/{spec['provider']}/{spec['id']}/{spec['authentication_model']}"
    require(spec["authentication_model"] in {"no-authentication", "shared-authentication", "individual-authentication"},
            "Unsupported authentication model")
    require(bool(spec.get("oauth")) == (spec["authentication_model"] == "individual-authentication"),
            "Individual authentication requires approved OAuth app metadata")
    require(re.fullmatch(r"[0-9a-f]{32}", spec["server_id"]), "Invalid server ID")
    require(spec["approved_tools"] and len(spec["approved_tools"]) == len(set(spec["approved_tools"])),
            "Explicit unique tool approval is required")
    gateways = catalog(api, "gateways")
    matches = [g for g in gateways if g.get("name") == alias or g.get("id") == spec.get("gateway_id")]
    require(len(matches) <= 1, "Conflicting native gateway alias or ID")
    gateway = matches[0] if matches else None
    require(gateway is not None or not spec.get("gateway_id"), "Configured gateway ID is absent")
    require(not any(address(g["url"]) == address(spec["upstream_url"]) and g is not gateway for g in gateways),
            "Upstream already belongs to another gateway")
    servers = [s for s in catalog(api, "servers") if s.get("id") == spec["server_id"] or s.get("name") == alias]
    require(len(servers) <= 1 and all(s["id"] == spec["server_id"] for s in servers), "Server alias or ID conflict")
    server = servers[0] if servers else None
    require(server is None or gateway is not None, "Server lost its gateway; refusing to replace")
    if gateway is None:
        payload = {"name": alias, "description": marker, "url": spec["upstream_url"],
                   "transport": spec["transport"], "auth_type": "none", "passthrough_headers": [],
                   "gateway_mode": "cache", "team_id": team, "visibility": spec["visibility"]}
        if spec.get("oauth"):
            require(spec["oauth"]["client_secret_ref"] == {"name": "contextforge-oauth-apps", "key": spec["id"]}
                    and spec["oauth"]["pkce"] is True, "Invalid OAuth Secret reference or PKCE")
            secret = Path("/oauth-apps", spec["id"]).read_text()
            require(secret.strip() and len(secret) <= 16384 and not any(ord(c) < 32 for c in secret),
                    "Invalid OAuth app credential; value hidden")
            payload.update(auth_type="oauth", oauth_config=native_oauth(spec) | {"client_secret": secret})
            secret = ""
        try:
            api.request("POST", "/gateways", payload)
        finally:
            payload.clear()
        matches = [g for g in catalog(api, "gateways") if g.get("name") == alias]
        require(len(matches) == 1, "Gateway creation not confirmed; retry same alias")
        gateway = matches[0]
    check_gateway(gateway, spec, team, owner, marker)
    actual = members(api, server, gateway, spec, team, owner, marker) if server else set()
    pending = False
    try:
        desired = approved(api, gateway, spec, team, owner)
    except PendingDiscovery:
        require(not actual, "Previously published OAuth tools disappeared; operator repair required")
        pending = True
        desired = set()
    if server is None:
        api.request("POST", "/servers", {"server": {"id": spec["server_id"], "name": alias,
                    "description": marker + "/" + gateway["id"], "associated_tools": sorted(desired),
                    "associated_resources": [], "associated_prompts": [], "associated_a2a_agents": [],
                    "oauth_enabled": False, "team_id": team, "visibility": spec["visibility"]},
                    "team_id": team, "visibility": spec["visibility"]})
    elif actual != desired:
        # Discovery is separate; only verified approved tools may fill an empty OAuth server.
        require(not actual and spec.get("oauth"), "Tool membership conflict; operator repair required")
        api.request("PUT", "/servers/" + spec["server_id"], {"associated_tools": sorted(desired)})
    server = api.request("GET", "/servers/" + spec["server_id"])
    require(members(api, server, gateway, spec, team, owner, marker) == desired, "Server tool verification failed")
    return {"id": spec["id"], "gateway_id": gateway["id"], "server_id": spec["server_id"],
            "approved_config_hash": config_hash(spec),
            "state": "pending-discovery" if pending else "published", "error_code": None}
