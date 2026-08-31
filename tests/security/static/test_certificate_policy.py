"""Static policy checks for external Gateway certificate issuance."""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

STRICT_GATEWAY_TEMPLATES = (
    ROOT / "charts/keycloak/server/templates/gateway.yaml",
    ROOT / "charts/agentgateway/templates/gateway-external.yaml",
    ROOT / "charts/librechat/app/templates/gateway.yaml",
    ROOT / "charts/dify/web/templates/gateway.yaml",
    ROOT / "charts/studio/web/templates/gateway.yaml",
    ROOT / "charts/langfuse/templates/gateway.yaml",
)
ADMIN_GATEWAY_TEMPLATE = ROOT / "charts/librechat/admin-panel/templates/gateway.yaml"
ADMIN_ROUTE_TEMPLATE = ROOT / "charts/librechat/admin-panel/templates/http-route.yaml"
ROOK_GATEWAY_TEMPLATE = ROOT / "charts/rook-ceph/templates/object-gateway.yaml"
GATEWAY_TEMPLATES = (
    *STRICT_GATEWAY_TEMPLATES,
    ADMIN_GATEWAY_TEMPLATE,
    ROOK_GATEWAY_TEMPLATE,
)
GATEWAY_CHARTS = (
    (
        ROOT / "charts/keycloak/server",
        "keycloak",
        "auth-keycloak",
        "auth-keycloak-gateway",
    ),
    (
        ROOT / "charts/agentgateway",
        "agentgateway",
        "infra-agentgateway",
        "infra-agentgateway-external-gateway",
    ),
    (
        ROOT / "charts/librechat/app",
        "librechat",
        "frontend-librechat",
        "frontend-librechat-gateway",
    ),
    (
        ROOT / "charts/librechat/admin-panel",
        "frontend-librechat-admin-panel",
        "frontend-librechat",
        "frontend-librechat-admin-panel",
    ),
    (
        ROOT / "charts/dify/web",
        "frontend-dify-web-gui",
        "frontend-dify",
        "frontend-dify-web-gui-gateway",
    ),
    (
        ROOT / "charts/studio/web",
        "studio",
        "frontend-studio",
        "frontend-studio-gateway",
    ),
    (
        ROOT / "charts/langfuse",
        "langfuse",
        "monitor-langfuse",
        "monitor-langfuse-gateway",
    ),
    (
        ROOT / "charts/rook-ceph",
        "rook-ceph",
        "infra-rook-ceph",
        "infra-rook-ceph-object-gateway",
    ),
)

APPROVAL_POLICY_CHART = ROOT / "charts/cert-manager/approval-policy"
APPROVER_POLICY_CHART = ROOT / "charts/cert-manager/approver-policy"
CONTROLLER_CHART = ROOT / "charts/cert-manager/controller"
LINT_VALUES = ROOT / "tests/validation/helm-lint-values.yaml"
CERT_MANAGER_ARCHIVE = (
    CONTROLLER_CHART / "charts/cert-manager-v1.20.2.tgz"
)
APPROVER_POLICY_ARCHIVE = (
    APPROVER_POLICY_CHART
    / "charts/cert-manager-approver-policy-v0.25.1.tgz"
)

DEFAULT_DURATION = "2160h"
DEFAULT_USAGES = ["digital signature", "key encipherment"]
PUBLIC_ISSUERS = {
    "staging": "letsencrypt-staging-cluster-issuer",
    "production": "letsencrypt-production-cluster-issuer",
}

INTERNAL_CERTIFICATES = {
    "cert-manager-internal-ca-bootstrap": (
        ROOT / "charts/cert-manager/internal-issuer",
        "infra-cert-manager-internal-issuer",
        "infra-cert-manager",
        "infra-cert-manager-internal-ca",
    ),
    "cert-manager-internal-openbao-server": (
        ROOT / "charts/openbao",
        "infra-openbao",
        "infra-openbao",
        "infra-openbao-server-certificate",
    ),
    "cert-manager-internal-opensearch-http": (
        ROOT / "charts/opensearch",
        "monitor-opensearch",
        "monitor-opensearch",
        "monitor-opensearch-http-certificate",
    ),
    "cert-manager-internal-opensearch-transport": (
        ROOT / "charts/opensearch",
        "monitor-opensearch",
        "monitor-opensearch",
        "monitor-opensearch-transport-certificate",
    ),
    "cert-manager-internal-opensearch-admin": (
        ROOT / "charts/opensearch",
        "monitor-opensearch",
        "monitor-opensearch",
        "monitor-opensearch-admin-certificate",
    ),
    "cert-manager-internal-pii-engine-server": (
        ROOT / "charts/pii-engine",
        "monitor-pii-engine",
        "monitor-pii-engine",
        "monitor-pii-engine-server-certificate",
    ),
    "cert-manager-internal-agentgateway-extproc-client": (
        ROOT / "charts/agentgateway-extproc",
        "monitor-agentgateway-extproc",
        "monitor-agentgateway-extproc",
        "monitor-agentgateway-extproc-engine-client-certificate",
    ),
    "cert-manager-internal-studio-pii-engine-client": (
        ROOT / "charts/studio/api",
        "frontend-studio-api",
        "frontend-studio",
        "frontend-studio-pii-engine-client-certificate",
    ),
    "cert-manager-internal-trust-manager-webhook": (
        ROOT / "charts/trust-manager",
        "infra-trust-manager",
        "infra-trust-manager",
        "trust-manager",
    ),
    "cert-manager-internal-postgres-auth": (
        ROOT / "charts/postgres/auth",
        "postgres-auth",
        "infra-postgres-auth",
        "postgres-auth-server",
    ),
    "cert-manager-internal-postgres-operations-documentdb": (
        ROOT / "charts/postgres/operations",
        "postgres-operations",
        "infra-postgres-operations",
        "postgres-operations-documentdb",
    ),
}

PUBLIC_HOSTNAMES = {
    "cert-manager-public-keycloak": "lint.example",
    "cert-manager-public-agentgateway": "agentgateway.lint.example",
    "cert-manager-public-librechat": "librechat.lint.example",
    "cert-manager-public-librechat-admin": "librechat-admin.lint.example",
    "cert-manager-public-dify": "dify.lint.example",
    "cert-manager-public-studio": "studio.lint.example",
    "cert-manager-public-langfuse": "langfuse.lint.example",
    "cert-manager-public-rook-object-gateway": "objects.lint.example",
}

POLICY_NAMES = {
    "cert-manager-internal-ca-bootstrap",
    "cert-manager-internal-openbao-server",
    "cert-manager-internal-opensearch-http",
    "cert-manager-internal-opensearch-transport",
    "cert-manager-internal-opensearch-admin",
    "cert-manager-internal-pii-engine-server",
    "cert-manager-internal-agentgateway-extproc-client",
    "cert-manager-internal-studio-pii-engine-client",
    "cert-manager-internal-trust-manager-webhook",
    "cert-manager-internal-postgres-auth",
    "cert-manager-internal-postgres-operations-documentdb",
    "cert-manager-deny-unmatched",
}
POLICY_NAMES.update(
    f"{policy_name}-{environment}"
    for policy_name in PUBLIC_HOSTNAMES
    for environment in PUBLIC_ISSUERS
)

ROUTE_TEMPLATES = tuple(path.with_name(path.name.replace("gateway", "http-route")) for path in STRICT_GATEWAY_TEMPLATES if "gateway-external" not in path.name)
ROUTE_TEMPLATES += (ROOT / "charts/agentgateway/templates/http-route-external.yaml",)


def render_chart(
    chart: Path,
    release: str,
    namespace: str = "infra-cert-manager",
    set_values: tuple[str, ...] = (),
) -> str:
    helm = shutil.which("helm")
    if helm is None:
        raise unittest.SkipTest("helm is required for rendered certificate policy tests")
    command = [
        helm,
        "template",
        release,
        str(chart),
        "--namespace",
        namespace,
        "--values",
        str(LINT_VALUES),
    ]
    for value in set_values:
        command.extend(("--set", value))
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout


def manifest_documents(rendered: str) -> list[str]:
    return [doc for doc in re.split(r"(?m)^---\s*$", rendered) if doc.strip()]


def document(rendered: str, kind: str, name: str) -> str:
    for doc in manifest_documents(rendered):
        metadata = doc.split("spec:\n", maxsplit=1)[0]
        if not re.search(rf"(?m)^kind: {re.escape(kind)}$", metadata):
            continue
        if re.search(rf"(?m)^  name: {re.escape(name)}$", metadata):
            return doc
    raise AssertionError(f"Missing {kind}/{name}")


def yaml_scalar(manifest: str, key: str, indent: int) -> str | None:
    match = re.search(rf"(?m)^{re.escape(' ' * indent + key)}:\s*([^#\n]+)", manifest)
    return match.group(1).strip().strip('"') if match else None


def yaml_list(manifest: str, key: str, indent: int) -> list[str] | None:
    lines = manifest.splitlines()
    marker = f"{' ' * indent}{key}:"
    for index, line in enumerate(lines):
        if line != marker:
            continue
        values: list[str] = []
        item_prefix = " " * (indent + 2) + "- "
        for item in lines[index + 1 :]:
            if item.startswith(item_prefix):
                values.append(item.removeprefix(item_prefix).strip().strip('"'))
                continue
            if item.strip() and len(item) - len(item.lstrip()) <= indent:
                break
        return values
    return None


def archive_member(archive: Path, member: str) -> str:
    with tarfile.open(archive) as package:
        extracted = package.extractfile(member)
        if extracted is None:
            raise AssertionError(f"Missing {member} in {archive}")
        return extracted.read().decode()


def release_graph() -> dict[tuple[str, str], list[tuple[str, str]]]:
    graph: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for path in ROOT.glob("releases/**/*.yaml"):
        text = path.read_text(encoding="utf-8")
        metadata = text.split("spec:\n", maxsplit=1)[0]
        if not re.search(r"(?m)^kind: HelmRelease$", metadata):
            continue
        name = re.search(r"(?m)^  name: ([^\n]+)$", metadata)
        namespace = re.search(r"(?m)^  namespace: ([^\n]+)$", metadata)
        if not name or not namespace:
            raise AssertionError(f"Could not parse HelmRelease metadata in {path}")
        depends_block = ""
        if "  dependsOn:\n" in text:
            depends_block = text.split("  dependsOn:\n", maxsplit=1)[1].split(
                "  chart:\n", maxsplit=1
            )[0]
        graph[(name.group(1), namespace.group(1))] = re.findall(
            r"(?m)^    - name: ([^\n]+)\n      namespace: ([^\n]+)$",
            depends_block,
        )
    return graph


class CertificatePolicyTests(unittest.TestCase):
    def test_cert_manager_disables_builtin_approval_and_aggregated_roles(self) -> None:
        rendered = render_chart(CONTROLLER_CHART, "infra-cert-manager-controller")
        self.assertIn("--controllers=-certificaterequests-approver", rendered)
        self.assertNotIn("cert-manager-controller-approve:cert-manager-io", rendered)
        self.assertNotIn("rbac.authorization.k8s.io/aggregate-to-admin", rendered)
        self.assertNotIn("rbac.authorization.k8s.io/aggregate-to-edit", rendered)

    def test_approver_policy_is_the_sole_approval_controller(self) -> None:
        controller = render_chart(CONTROLLER_CHART, "infra-cert-manager-controller")
        approver = render_chart(APPROVER_POLICY_CHART, "infra-cert-manager-approver-policy")
        role = document(approver, "ClusterRole", "cert-manager-approver-policy")
        self.assertEqual((controller + approver).count('verbs: ["approve"]'), 1)
        self.assertIn('   - "issuers.cert-manager.io/*"', role)
        self.assertIn('   - "clusterissuers.cert-manager.io/*"', role)
        self.assertNotIn('   - "*"', role)
        self.assertNotIn("kind: Certificate\n", approver)
        self.assertIn("name: cert-manager-approver-policy-tls", approver)

    def test_pinned_default_and_approver_duration_contracts(self) -> None:
        certificate_crd = archive_member(
            CERT_MANAGER_ARCHIVE,
            "cert-manager/templates/crd-cert-manager.io_certificates.yaml",
        )
        self.assertIn("If unset, this defaults to 90 days.", certificate_crd)
        self.assertIn(
            "If unset, defaults to `digital signature` and `key encipherment`.",
            certificate_crd,
        )
        self.assertEqual(DEFAULT_DURATION, f"{90 * 24}h")

        approver_crd = archive_member(
            APPROVER_POLICY_ARCHIVE,
            "cert-manager-approver-policy/templates/"
            "crd-policy.cert-manager.io_certificaterequestpolicies.yaml",
        )
        self.assertIn(
            "If set, a duration _must_ be requested in the CertificateRequest.",
            approver_crd,
        )
        usages_schema = approver_crd.split(
            "                    usages:", maxsplit=1
        )[1].split("                  type: object", maxsplit=1)[0]
        self.assertNotIn("required:", usages_schema)

    def test_internal_profiles_match_rendered_certificate_requests(self) -> None:
        policies = render_chart(
            APPROVAL_POLICY_CHART, "infra-cert-manager-approval-policy"
        )
        rendered_charts: dict[tuple[Path, str, str], str] = {}

        for policy_name, (chart, release, namespace, certificate_name) in (
            INTERNAL_CERTIFICATES.items()
        ):
            with self.subTest(policy=policy_name):
                chart_key = (chart, release, namespace)
                if chart_key not in rendered_charts:
                    rendered_charts[chart_key] = render_chart(
                        chart, release, namespace
                    )
                certificate = document(
                    rendered_charts[chart_key], "Certificate", certificate_name
                )
                policy = document(
                    policies, "CertificateRequestPolicy", policy_name
                )

                explicit_usages = yaml_list(certificate, "usages", 2)
                expected_usages = list(explicit_usages or DEFAULT_USAGES)
                if yaml_scalar(certificate, "isCA", 2) == "true":
                    expected_usages.append("cert sign")
                self.assertEqual(
                    yaml_list(policy, "usages", 4), expected_usages
                )

                requested_duration = yaml_scalar(certificate, "duration", 2)
                min_duration = yaml_scalar(policy, "minDuration", 4)
                max_duration = yaml_scalar(policy, "maxDuration", 4)
                if requested_duration is not None:
                    self.assertEqual(min_duration, requested_duration)
                    self.assertEqual(max_duration, requested_duration)
                else:
                    self.assertEqual(DEFAULT_DURATION, "2160h")
                    self.assertIsNone(min_duration)
                    self.assertIsNone(max_duration)
                    self.assertIn(
                        "approver-policy duration constraints reject nil requests",
                        policy,
                    )

    def test_policy_inventory_and_public_hostnames_are_exact(self) -> None:
        rendered = render_chart(APPROVAL_POLICY_CHART, "infra-cert-manager-approval-policy")
        names = {
            match.group(1)
            for doc in manifest_documents(rendered)
            if re.search(r"(?m)^kind: CertificateRequestPolicy$", doc)
            if (match := re.search(r"(?m)^  name: ([^\n]+)$", doc))
        }
        self.assertEqual(names, POLICY_NAMES)

        for policy_name, hostname in PUBLIC_HOSTNAMES.items():
            for environment, issuer in PUBLIC_ISSUERS.items():
                full_name = f"{policy_name}-{environment}"
                with self.subTest(policy=full_name):
                    policy = document(rendered, "CertificateRequestPolicy", full_name)
                    self.assertIn(f'- "{hostname}"', policy)
                    self.assertIn(f'name: "{issuer}"', policy)
                    self.assertIn("kind: ClusterIssuer", policy)
                    self.assertIn("algorithm: RSA", policy)
                    self.assertIn("minSize: 2048\n      maxSize: 2048", policy)
                    self.assertEqual(yaml_list(policy, "usages", 4), DEFAULT_USAGES)
                    self.assertEqual(
                        yaml_scalar(policy, "minDuration", 4), DEFAULT_DURATION
                    )
                    self.assertEqual(
                        yaml_scalar(policy, "maxDuration", 4), DEFAULT_DURATION
                    )

    def test_gateways_request_policy_bounded_duration_and_default_usages(self) -> None:
        gateways = (
            *GATEWAY_TEMPLATES,
            ROOT / "charts/rook-ceph/templates/object-gateway.yaml",
        )
        for gateway in gateways:
            content = gateway.read_text(encoding="utf-8")
            with self.subTest(gateway=gateway):
                self.assertIn('cert-manager.io/duration: "2160h"', content)
                self.assertNotIn("cert-manager.io/usages", content)

    def test_only_cert_manager_controller_service_account_can_use_policies(self) -> None:
        rendered = render_chart(APPROVAL_POLICY_CHART, "infra-cert-manager-approval-policy")
        role = document(rendered, "ClusterRole", "cert-manager-approval-policy-use")
        binding = document(
            rendered, "ClusterRoleBinding", "cert-manager-approval-policy-use"
        )
        self.assertEqual(rendered.count("verbs: [use]"), 1)
        self.assertEqual(role.count("      - cert-manager-"), len(POLICY_NAMES))
        self.assertIn("kind: ServiceAccount", binding)
        self.assertIn("name: infra-cert-manager-controller", binding)
        self.assertIn("namespace: infra-cert-manager", binding)
        self.assertNotIn("kind: Group", binding)
        self.assertNotIn("kind: User", binding)

    def test_unmatched_cert_manager_signers_are_explicitly_denied(self) -> None:
        rendered = render_chart(APPROVAL_POLICY_CHART, "infra-cert-manager-approval-policy")
        policy = document(rendered, "CertificateRequestPolicy", "cert-manager-deny-unmatched")
        self.assertIn('value: ""\n      required: true', policy)
        self.assertIn('kind: "*Issuer"', policy)
        self.assertIn('name: "*"', policy)
        self.assertIn("namespace: {}", policy)

    def test_release_chain_installs_policies_before_all_request_producers(self) -> None:
        graph = release_graph()
        controller = ("cert-manager-controller", "infra-cert-manager")
        approver = ("cert-manager-approver-policy", "infra-cert-manager")
        policies = ("cert-manager-approval-policy", "infra-cert-manager")
        self.assertEqual(graph[approver], [controller])
        self.assertEqual(graph[policies], [approver])

        request_producers = {
            ("cert-manager-internal-issuer", "infra-cert-manager"),
            ("cert-manager-issuers", "infra-cert-manager"),
            ("trust-manager", "infra-trust-manager"),
            ("openbao", "infra-openbao"),
            ("opensearch", "monitor-opensearch"),
            ("pii-engine", "monitor-pii-engine"),
            ("agentgateway-extproc", "monitor-agentgateway-extproc"),
            ("studio-api", "frontend-studio"),
            ("keycloak", "auth-keycloak"),
            ("agentgateway", "infra-agentgateway"),
            ("librechat", "frontend-librechat"),
            ("frontend-librechat-admin-panel", "frontend-librechat"),
            ("frontend-dify-web-gui", "frontend-dify"),
            ("studio", "frontend-studio"),
            ("langfuse", "monitor-langfuse"),
            ("rook-ceph", "infra-rook-ceph"),
            ("postgres-auth", "infra-postgres-auth"),
            ("postgres-operations", "infra-postgres-operations"),
        }

        def ancestors(release: tuple[str, str]) -> set[tuple[str, str]]:
            result: set[tuple[str, str]] = set()
            for dependency in graph[release]:
                result.add(dependency)
                result.update(ancestors(dependency))
            return result

        for release in request_producers:
            with self.subTest(release=release):
                self.assertIn(policies, ancestors(release))

    def test_external_gateways_are_disabled_and_use_staging_by_default(self) -> None:
        for chart in GATEWAY_TEMPLATES:
            values = chart.parent.parent / "values.yaml"
            content = values.read_text()
            self.assertRegex(content, r"externalGateway:\s*\n\s+enabled: false")
            self.assertRegex(
                content, r"publicCertificates:\s*\n\s+useProduction: false"
            )

    def test_gateways_validate_hostname_and_derive_public_issuer(self) -> None:
        for path in STRICT_GATEWAY_TEMPLATES:
            content = path.read_text()
            self.assertIn("if .Values.externalGateway.enabled", content, path)
            self.assertIn('required "external Gateway hostname is required"', content, path)
            self.assertIn("place.holder cannot be used", content, path)
            self.assertIn("publicCertificates.useProduction", content, path)
            for issuer in PUBLIC_ISSUERS.values():
                self.assertIn(issuer, content, path)
            self.assertNotIn("externalGateway.clusterIssuer", content, path)

    def test_all_gateways_switch_between_exact_public_issuers(self) -> None:
        for chart, release, namespace, gateway_name in GATEWAY_CHARTS:
            with self.subTest(chart=chart, use_production=False):
                staging = document(
                    render_chart(chart, release, namespace), "Gateway", gateway_name
                )
                self.assertIn(
                    'cert-manager.io/cluster-issuer: "letsencrypt-staging-cluster-issuer"',
                    staging,
                )
            with self.subTest(chart=chart, use_production=True):
                production = document(
                    render_chart(
                        chart,
                        release,
                        namespace,
                        ("publicCertificates.useProduction=true",),
                    ),
                    "Gateway",
                    gateway_name,
                )
                self.assertIn(
                    'cert-manager.io/cluster-issuer: "letsencrypt-production-cluster-issuer"',
                    production,
                )

    def test_routes_use_the_same_enable_and_placeholder_guards(self) -> None:
        for path in ROUTE_TEMPLATES:
            content = path.read_text()
            self.assertIn("if .Values.externalGateway.enabled", content, path)
            self.assertIn('required "external Gateway hostname is required"', content, path)
            self.assertIn("place.holder cannot be used", content, path)

    def test_admin_gateway_and_route_suppress_blank_or_placeholder_hostnames(self) -> None:
        helpers = (
            ROOT / "charts/librechat/admin-panel/templates/_helpers.tpl"
        ).read_text()
        self.assertIn('(ne $hostname "place.holder")', helpers)
        self.assertIn('(not (hasSuffix ".place.holder" $hostname))', helpers)
        for path in (ADMIN_GATEWAY_TEMPLATE, ADMIN_ROUTE_TEMPLATE):
            content = path.read_text()
            with self.subTest(path=path):
                self.assertIn("if .Values.externalGateway.enabled", content)
                self.assertIn(
                    'eq (include "librechat-admin-panel.validHostname" $hostname) "true"',
                    content,
                )
        gateway = ADMIN_GATEWAY_TEMPLATE.read_text()
        self.assertIn("publicCertificates.useProduction", gateway)
        for issuer in PUBLIC_ISSUERS.values():
            self.assertIn(issuer, gateway)
        self.assertNotIn("externalGateway.clusterIssuer", gateway)

    def test_issuers_are_not_helm_hooks_and_require_dns_zones(self) -> None:
        path = ROOT / "charts/cert-manager/issuers/templates/issuers.yaml"
        content = path.read_text()
        self.assertNotIn("helm.sh/hook", content)
        self.assertEqual(content.count("dnsZones:"), 2)
        self.assertIn("certManager.dnsZones is required", content)
        self.assertIn("certManager.email is required", content)

    def test_external_release_hostname_overrides_are_required(self) -> None:
        releases = (
            ROOT / "releases/keycloak/server.yaml",
            ROOT / "releases/agentgateway/app.yaml",
            ROOT / "releases/librechat/core/app.yaml",
            ROOT / "releases/librechat/core/admin-panel.yaml",
            ROOT / "releases/dify/web.yaml",
            ROOT / "releases/studio/web.yaml",
            ROOT / "releases/langfuse/app.yaml",
            ROOT / "releases/rook-ceph/app.yaml",
        )
        for path in releases:
            content = path.read_text()
            match = re.search(
                r"name: client-values(?P<body>.*?)(?=\n\s*- kind:|\Z)",
                content,
                re.DOTALL,
            )
            self.assertIsNotNone(match, path)
            self.assertNotIn("optional: true", match.group("body"), path)


if __name__ == "__main__":
    unittest.main()
