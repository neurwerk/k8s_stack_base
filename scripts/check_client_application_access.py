#!/usr/bin/env python3
"""Derive an offline access plan from selected local client/platform composition."""

from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from .access_composition import Composition, document, leaf, local, mapping, source
    from .access_git import GitSnapshot
    from .check_application_access import BROWSER_DEPENDENCIES, PlanError, validate_plan
else:
    from access_composition import Composition, document, leaf, local, mapping, source
    from access_git import GitSnapshot
    from check_application_access import BROWSER_DEPENDENCIES, PlanError, validate_plan


# Fixed existing same-origin bundles, not discovery from arbitrary chart templates.
BUNDLES = {
    "keycloak": ("authKeycloak.hostname", ("keycloak/server",)),
    "librechat": ("frontendLibrechat.hostname", ("librechat/app", "librechat/shared")),
    "librechat-admin": ("frontendLibrechat.adminPanel.hostname", ("librechat/admin-panel",)),
    "studio": ("frontendStudio.studio.hostname", ("studio/web", "studio/api")),
    "dify": ("frontendDify.hostname", ("dify/web", "dify/api", "dify/shared")),
    "langfuse": ("monitorLangfuseWrapper.hostname", ("langfuse",)),
    "agentgateway": ("infraAgentgatewayWrapper.hostname", ("agentgateway",)),
    "forgejo": ("forgejo.hostname", ("forgejo",)),
}
CALLBACKS = {"librechat": "/oauth/openid/callback", "studio": "/auth/callback",
             "dify": "/console/api/oauth/authorize/keycloak", "agentgateway": None}
GATEWAY_PORTS = {
    "keycloak": "authKeycloak.keycloak.ports.gateway.tls", "studio": "frontendStudio.studio.ports.gateway.tls",
    "dify": "frontendDify.webGui.ports.gateway.tls", "langfuse": "monitorLangfuseWrapper.ports.gateway.tls",
    "agentgateway": "infraAgentgatewayWrapper.ports.gateway.tls",
}
# Explicit exclusions: operator/control plane, backend-only products, and jobs.
# Maintenance packages only static infrastructure and inert operator templates.
# WireGuard's UDP transport is not a browser application, even with NodePort;
# classification does not verify its outer exposure, peers or firewall grants.
# Docling is a private ClusterIP backend with no public route option.
NON_ENDPOINT_CHARTS = set("""
agentgateway-extproc docling cert-manager/approver-policy cert-manager/issuers
cert-manager/approval-policy cert-manager/controller cert-manager/internal-issuer
external-secrets fluent-bit fluent-bit/shared kube-prometheus-stack wireguard
openbao openbao/operator opensearch pii-engine pii-engine-model-sync
postgres/auth postgres/operations reloader traefik trust-manager studio/shared maintenance
keycloak-api-key-bridge keycloak/realm-config/active-directory
keycloak/realm-config/initial-admin keycloak/realm-config/realm-roles
keycloak/oidc/keycloak-api-key-bridge keycloak/oidc/dify-agentgateway
librechat/valkey librechat/meilisearch librechat/rag-api
librechat/code-interpreter/shared librechat/code-interpreter/api
librechat/code-interpreter/tool-call-server librechat/code-interpreter/egress-gateway
librechat/code-interpreter/package-init librechat/code-interpreter/valkey
librechat/code-interpreter/file-server librechat/code-interpreter/worker
dify/beat dify/plugin-daemon dify/redis dify/sandbox dify/worker
""".split())
DISCLAIMER = "Offline planning only; declared producer contracts, not actual Secret sync or tampering verification. No runtime enforcement, DNS, certificate trust or signature verification. No changes made."
BASE_URL = "https://github.com/neurwerk/k8s_stack_base.git"
# These known alternate surfaces are not represented by the endpoint catalog.
NO_INGRESS = {
    "langfuse": ("langfuse.langfuse.ingress.enabled",),
    "kube-prometheus-stack": tuple("kube-prometheus-stack." + name + ".ingress.enabled" for name in ("grafana", "prometheus", "alertmanager")),
    "opensearch": ("opensearch.ingress.enabled",),
    "openbao": ("openbao.server.ingress.enabled", "openbao.server.route.enabled",
                "openbao.server.gateway.httpRoute.enabled", "openbao.server.gateway.tlsRoute.enabled"),
}


def revisions(comp: Composition) -> dict[str, str]:
    platform = comp.platform_snapshot
    sources = [obj for obj in comp.objects.values() if obj["kind"] == "GitRepository"]
    selected = []
    for obj in sources:
        if obj["apiVersion"] != "source.toolkit.fluxcd.io/v1":
            raise PlanError("revision: unsupported GitRepository version")
        spec = mapping(obj.get("spec"), "url ref", "interval timeout secretRef verify suspend recurseSubmodules ignore")
        if any(spec.get(key) for key in ("suspend", "recurseSubmodules", "ignore")):
            raise PlanError("revision: unsupported source artifact modification")
        if obj["metadata"]["name"] == "k8s-stack":
            if obj["metadata"].get("namespace") != "flux-system" or spec["url"] != BASE_URL:
                raise PlanError("revision: unsupported platform source URL or namespace identity")
            selected.append(spec["ref"])
    if len(selected) != 1:
        raise PlanError("revision: exactly one selected platform source required")
    ref = mapping(selected[0], "", "branch tag commit")
    sha = platform.sha
    if platform.modified():
        raise PlanError("revision: supplied platform snapshot must be clean")
    if set(ref) == {"tag"} and type(ref["tag"]) is str and re.fullmatch(r"v\d+\.\d+\.\d+", ref["tag"]):
        if platform.resolve(f"refs/tags/{ref['tag']}") != sha:
            raise PlanError("revision: supplied platform HEAD does not match selected local tag")
        selection = ref["tag"]
    elif ref == {"branch": "main"}:
        if platform.resolve("refs/heads/main") != sha:
            raise PlanError("revision: supplied platform HEAD does not match local main")
        selection = "main"
    elif set(ref) == {"commit"} and type(ref["commit"]) is str and re.fullmatch(r"[0-9a-f]{40}", ref["commit"]):
        if ref["commit"] != sha:
            raise PlanError("revision: supplied platform HEAD does not match selected commit")
        selection = ref["commit"]
    else:
        raise PlanError("revision: only exact stable tags, main or full alpha commits are supported")
    client = GitSnapshot(comp.roots["flux-system"])
    checker = GitSnapshot(Path(__file__).resolve().parent.parent)
    return {"platform": sha, "selection": selection, "client": client.sha,
            "clientSnapshot": "modified" if client.modified(comp.client_inputs) else "clean",
            "checker": checker.sha, "checkerSnapshot": "modified" if checker.modified() else "clean"}


def hostname(value: str, endpoint: str) -> str:
    if len(value) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", value) or any(len(part) > 63 for part in value.split(".")) or value.endswith((".invalid", "place.holder")):
        raise PlanError(f"{endpoint}: missing or unsupported canonical hostname")
    return "https://" + value


def origin(value: str, endpoint: str) -> str:
    try:
        url = urlsplit(value)
        if url.scheme != "https" or url.username or url.password or url.port not in (None, 443) or url.query or url.fragment or any(c.isspace() for c in value):
            raise ValueError
        result = hostname(url.hostname or "", endpoint)
        if url.netloc not in (url.hostname, f"{url.hostname}:443"):
            raise ValueError
        return result
    except ValueError:
        raise PlanError(f"{endpoint}: unsupported advertised HTTPS URL/origin") from None


def derive_plan(client_root: Path, platform_root: Path, cluster: str = "prod-eu-1") -> tuple[dict, dict]:
    """Return (normalized plan, revision identities); never mutate or resolve Secrets.

    The policy is always config/application-access.yaml under the supplied client.
    Dependency and schema errors raise PlanError, with no supplied values echoed.
    """
    comp = Composition(client_root, platform_root, cluster, GitSnapshot(platform_root))
    policy = document(comp.read(local(comp.roots["flux-system"], comp.roots["flux-system"], "config/application-access.yaml"), "flux-system"))
    mapping(policy, "access endpoints")
    mapping(policy["access"], "boundary default")
    mapping(policy["endpoints"], "", " ".join(BROWSER_DEPENDENCIES))
    for entry in policy["endpoints"].values():
        mapping(entry, "", "level devices")
    charts = {}
    known = NON_ENDPOINT_CHARTS | {c for _, group in BUNDLES.values() for c in group} | {"rook-ceph", "keycloak/oidc/forgejo"} | {"keycloak/oidc/" + c for c in CALLBACKS}
    for obj in comp.objects.values():
        if obj["kind"] != "HelmRelease":
            continue
        if obj["apiVersion"] != "helm.toolkit.fluxcd.io/v2":
            raise PlanError("composition: unsupported HelmRelease version")
        spec = mapping(obj.get("spec"), "chart", "releaseName interval timeout install upgrade dependsOn valuesFrom values suspend maxHistory driftDetection test uninstall rollback waitStrategy healthCheckExprs targetNamespace")
        if spec.get("targetNamespace", obj["metadata"].get("namespace", "default")) != obj["metadata"].get("namespace", "default"):
            raise PlanError("composition: alternate targetNamespace unsupported")
        if spec.get("suspend", False) is not False:
            raise PlanError("composition: suspended release unsupported, not absent")
        for action in ("install", "upgrade"):
            options = spec.get(action, {})
            if type(options) is not dict or any(options.get(key, False) is not False for key in ("preserveValues", "disableHooks")):
                raise PlanError("composition: runtime value reuse or disabled hooks unsupported")
        chart = mapping(mapping(spec["chart"], "spec")["spec"], "chart sourceRef", "interval reconcileStrategy")
        if source(chart["sourceRef"], obj["metadata"].get("namespace", "default")) != "k8s-stack":
            raise PlanError("composition: charts must use supplied platform source")
        root = comp.roots["k8s-stack"]
        path = local(root, root, chart["chart"])
        chart_name = path.relative_to(root).as_posix().removeprefix("charts/")
        if not path.is_relative_to(root / "charts") or chart_name not in known:
            raise PlanError("composition: unknown selected chart; endpoint classification required")
        if chart_name in charts:
            raise PlanError("composition: duplicate selected chart identity")
        charts[chart_name] = comp.layers(obj, chart_name) if chart_name not in NON_ENDPOINT_CHARTS or chart_name in NO_INGRESS else None
        for field in NO_INGRESS.get(chart_name, ()):
            if leaf(charts[chart_name], field, bool, "langfuse" if chart_name == "langfuse" else "composition"):
                raise PlanError("composition: alternate ingress/route is unclassified and unsupported")

    # Include the policy and all lazy values reads in candidate snapshot reporting.
    identities = revisions(comp)
    endpoints, origins, modes = {}, {}, set()

    def get(chart: str, path: str, expected: type = str, endpoint: str = "composition", **kwargs):
        if chart not in charts:
            raise PlanError(f"{endpoint}: required bundle component missing")
        return leaf(charts[chart], path, expected, endpoint, **kwargs)

    def align(value: str, name: str, target: str | None = None) -> None:
        if origin(value, name) != origins.get(target or name):
            raise PlanError(f"{name}: advertised origin disagrees with canonical endpoint")

    def issuer(chart: str, name: str) -> str:
        host = get(chart, "authKeycloak.hostname", endpoint=name)
        align(hostname(host, name), name, "keycloak")
        realm = get(chart, "authKeycloak.realm", endpoint=name)
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", realm):
            raise PlanError(f"{name}: unsupported OIDC realm")
        if realm != get("keycloak/server", "authKeycloak.realm", endpoint=name):
            raise PlanError(f"{name}: OIDC realm disagrees with selected Keycloak")
        return hostname(host, name) + "/realms/" + realm

    for name, (field, bundle) in BUNDLES.items():
        if not any(c in charts for c in bundle):
            continue
        if name == "forgejo" and not get("forgejo", "forgejo.enabled", bool, name):
            continue
        # Dify shared supplies issuer facts, not the browser hostname.
        hosts = [hostname(get(c, field, endpoint=name), name) for c in bundle if c != "dify/shared"]
        if len(set(hosts)) != 1:
            raise PlanError(f"{name}: same-origin bundle hostname mismatch")
        origins[name] = hosts[0]
        endpoints[name] = {}
        if not get(bundle[0], "publicCertificates.useProduction", bool, name):
            raise PlanError(f"{name}: public production certificates required")
        if get(bundle[0], "externalGateway.enabled", bool, name) and get(bundle[0], GATEWAY_PORTS.get(name, "externalGateway.port"), int, name) != 443:
            raise PlanError(f"{name}: noncanonical Gateway port unsupported")
        for c in bundle:
            if c in ("librechat/app", "librechat/shared", "dify/api", "forgejo"):
                modes.add(get(c, "canonicalEndpointRouting.mode", endpoint=name))

    if "rook-ceph" in charts and get("rook-ceph", "infraRookCeph.objectStore.externalGateway.enabled", bool, "librechat-files"):
        endpoints["librechat-files"] = {}
        origins["librechat-files"] = hostname(get("rook-ceph", "infraRookCeph.objectStore.publicHostname", endpoint="librechat-files"), "librechat-files")
        if not get("rook-ceph", "publicCertificates.useProduction", bool, "librechat-files"):
            raise PlanError("librechat-files: public production certificates required")
        if get("rook-ceph", "infraRookCeph.objectStore.externalGateway.port", int, "librechat-files") != 443:
            raise PlanError("librechat-files: noncanonical Gateway port unsupported")
    if "librechat" in endpoints:
        files = [get(c, "frontendLibrechat.objectStorage.enabled", bool, "librechat") for c in ("librechat/app", "librechat/shared")]
        if files[0] != files[1]:
            raise PlanError("librechat: app/shared file feature mismatch")
        endpoints["librechat"]["features"] = {"files": files[0]}
        for c in ("librechat/app", "librechat/shared"):
            issuer(c, "librechat")
        if files[0]:
            c, name = "librechat/app", "librechat-files"
            if not get(c, "frontendLibrechat.objectStorage.forcePathStyle", bool, name):
                raise PlanError("librechat-files: virtual-hosted bucket addressing unsupported")
            canonical = hostname(get(c, "infraRookCeph.objectStore.publicHostname", endpoint=name), name)
            override = get(c, "frontendLibrechat.objectStorage.endpoint", endpoint=name)
            effective = origin(override, name) if override else canonical
            if effective != canonical or (override and urlsplit(override).path not in ("", "/")):
                raise PlanError("librechat-files: unclassified external storage override")
            if name in origins and origins[name] != effective:
                raise PlanError("librechat-files: application and selected Rook route disagree")
            endpoints[name], origins[name] = {}, effective
        advertised = get("librechat/app", "frontendLibrechat.adminPanel.url", endpoint="librechat")
        admin_host = get("librechat/app", "frontendLibrechat.adminPanel.hostname", endpoint="librechat")
        if advertised or admin_host:
            align(advertised or hostname(admin_host, "librechat-admin"), "librechat", "librechat-admin")
    if "librechat-admin" in endpoints:
        align(hostname(get("librechat/admin-panel", "frontendLibrechat.hostname", endpoint="librechat-admin"), "librechat-admin"), "librechat-admin", "librechat")
    if "studio" in endpoints:
        authority = get("studio/web", "frontendStudio.studio.oidcAuthority", endpoint="studio")
        expected = issuer("studio/web", "studio")
        if authority.rstrip("/") != expected:
            raise PlanError("studio: OIDC authority disagrees with canonical issuer")
    if "dify" in endpoints:
        flag = get("dify/api", "frontendDify.config.ENABLE_SOCIAL_OAUTH_LOGIN", endpoint="dify")
        if flag not in ("true", "false"):
            raise PlanError("dify: consoleSSO requires string boolean true or false")
        endpoints["dify"]["features"] = {"consoleSSO": flag == "true"}
        if flag == "true":
            issuer("dify/shared", "dify")
    if "langfuse" in endpoints:
        align(get("langfuse", "langfuse.langfuse.nextauth.url", endpoint="langfuse"), "langfuse")
    if "forgejo" in endpoints:
        issuer("forgejo", "forgejo")
        issuer("keycloak/oidc/forgejo", "forgejo")
        if not get("keycloak/oidc/forgejo", "forgejo.enabled", bool, "forgejo"):
            raise PlanError("forgejo: selected OIDC callback disabled")
        align(hostname(get("keycloak/oidc/forgejo", "forgejo.hostname", endpoint="forgejo"), "forgejo"), "forgejo")
    for name, callback_path in CALLBACKS.items():
        chart = "keycloak/oidc/" + name
        enabled = name in endpoints and (name != "dify" or endpoints[name]["features"]["consoleSSO"])
        if not enabled:
            continue
        redirect = get(chart, "authKeycloak." + name + "RedirectUri", endpoint=name)
        web_origin = get(chart, "authKeycloak." + name + "WebOrigin", endpoint=name)
        if name == "agentgateway":
            if redirect or web_origin:
                raise PlanError("agentgateway: browser callback mode unsupported; native registration requires an empty pair")
            continue
        issuer(chart, name)
        if not redirect or not web_origin:
            raise PlanError(f"{name}: callback and web origin must be a nonempty pair")
        align(redirect, name)
        align(web_origin, name)
        if (callback_path is not None and urlsplit(redirect).path != callback_path) or urlsplit(web_origin).path != "":
            raise PlanError(f"{name}: callback or web origin path disagrees with endpoint contract")
        if name == "librechat":
            for suffix in ("RedirectUri", "WebOrigin"):
                value = get(chart, "authKeycloak.librechatAdmin" + suffix, endpoint="librechat-admin")
                if value or "librechat-admin" in endpoints:
                    align(value, "librechat-admin", "librechat" if suffix == "RedirectUri" else "librechat-admin")
                    expected = "/api/admin/oauth/openid/callback" if suffix == "RedirectUri" else ""
                    if urlsplit(value).path != expected:
                        raise PlanError("librechat-admin: callback or web origin path disagrees with endpoint contract")
    if set(endpoints) != set(policy["endpoints"]):
        missing = sorted(set(endpoints) - set(policy["endpoints"]))
        extra = sorted(set(policy["endpoints"]) - set(endpoints))
        raise PlanError("policy endpoint inventory mismatch; missing: " + ", ".join(missing) + "; not selected: " + ", ".join(extra))
    for name, fields in endpoints.items():
        fields.update(copy.deepcopy(policy["endpoints"][name]))
    # A routing observation is required; do not invent a client-wide mode.
    if not modes:
        for name, (_, bundle) in BUNDLES.items():
            if name in endpoints:
                modes.add(get(bundle[0], "canonicalEndpointRouting.mode", endpoint=name))
    if len(modes) != 1:
        raise PlanError("composition: canonical routing observations missing or inconsistent")
    plan = {"access": copy.deepcopy(policy["access"]), "endpoints": endpoints,
            "certificates": {"profile": "public-production"}, "canonicalEndpointRouting": {"mode": modes.pop()}}
    errors, _ = validate_plan(plan)
    if errors:
        raise PlanError("; ".join(errors))
    for name, host in origins.items():
        for other, other_host in origins.items():
            if name >= other or host != other_host:
                continue
            left, right = endpoints[name], endpoints[other]
            if (left.get("level", plan["access"]["default"]) != right.get("level", plan["access"]["default"])
                    or set(left.get("devices", [])) != set(right.get("devices", []))):
                raise PlanError(f"{name}, {other}: same-origin endpoints require identical access and device grants")
    return plan, identities


def main(argv: list[str] | None = None) -> int:
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(2, "ERROR: invalid CLI arguments; contents withheld\n" + DISCLAIMER + "\n")

    parser = Parser(description=__doc__)
    parser.add_argument("--client-root", type=Path, required=True)
    parser.add_argument("--platform-root", type=Path, required=True)
    parser.add_argument("--cluster", default="prod-eu-1")
    args = parser.parse_args(argv)
    try:
        plan, identities = derive_plan(args.client_root, args.platform_root, args.cluster)
        _, warnings = validate_plan(plan)
        for warning in warnings:
            print("WARNING: " + warning, file=sys.stderr)
        print("Access plan valid; endpoints: " + ", ".join(sorted(plan["endpoints"])))
        for name, value in identities.items():
            print(f"{name}: {value}")
    except (PlanError, RecursionError) as error:
        print("ERROR: " + (str(error) if isinstance(error, PlanError) else "composition nesting unsupported"), file=sys.stderr)
        print(DISCLAIMER, file=sys.stderr)
        return 1
    print(DISCLAIMER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
