"""Validate the complete Flux HelmRelease dependency graph."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INFRASTRUCTURE_STAGE = ROOT / "releases/infrastructure/kustomization.yaml"
APPLICATION_STAGE = ROOT / "releases/applications/kustomization.yaml"
TRUST_MANAGER_RELEASE = ROOT / "releases/trust-manager/app.yaml"
RELEASES = {
    name: ROOT / f"releases/{name}/app.yaml"
    for name in (
        "external-secrets",
        "kube-prometheus-stack",
        "openbao",
        "opensearch",
        "pii-engine-model-sync",
        "trust-manager",
    )
}


def release_graph() -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Parse every base HelmRelease and return its dependency adjacency list."""
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
        dependencies = re.findall(
            r"(?m)^    - name: ([^\n]+)\n      namespace: ([^\n]+)$",
            depends_block,
        )
        key = (name.group(1), namespace.group(1))
        if key in graph:
            raise AssertionError(f"Duplicate HelmRelease identity {key}")
        graph[key] = dependencies
    return graph


def stage_releases(path: Path) -> set[tuple[str, str]]:
    """Return HelmRelease identities owned by one stage Kustomization."""
    releases: set[tuple[str, str]] = set()

    def collect(target: Path) -> None:
        manifest = target / "kustomization.yaml" if target.is_dir() else target
        text = manifest.read_text(encoding="utf-8")
        metadata = text.split("spec:\n", maxsplit=1)[0]
        if re.search(r"(?m)^kind: HelmRelease$", metadata):
            name = re.search(r"(?m)^  name: ([^\n]+)$", metadata)
            namespace = re.search(r"(?m)^  namespace: ([^\n]+)$", metadata)
            if not name or not namespace:
                raise AssertionError(f"Could not parse HelmRelease metadata in {manifest}")
            releases.add((name.group(1), namespace.group(1)))
            return
        if "resources:\n" not in text:
            return
        resources_block = text.split("resources:\n", maxsplit=1)[1]
        resources_block = re.split(r"(?m)^[a-zA-Z]", resources_block, maxsplit=1)[0]
        resources = re.findall(r"(?m)^  - ([^\n]+)$", resources_block)
        for resource in resources:
            collect((manifest.parent / resource).resolve())

    collect(path)
    return releases


class HelmReleaseDagTests(unittest.TestCase):
    """Keep all declared dependencies resolvable and acyclic."""

    def test_chart_namespace_is_nested_under_source_ref(self) -> None:
        for path in ROOT.glob("releases/**/app.yaml"):
            text = path.read_text(encoding="utf-8")
            if not re.search(r"(?m)^kind: HelmRelease$", text):
                continue
            chart_block = text.split("  chart:\n", maxsplit=1)[1]
            chart_block = re.split(r"(?m)^  [a-zA-Z]", chart_block, maxsplit=1)[0]
            self.assertNotRegex(chart_block, r"(?m)^      namespace:", str(path))

    def test_all_dependencies_exist(self) -> None:
        graph = release_graph()
        missing = {
            dependency
            for dependencies in graph.values()
            for dependency in dependencies
            if dependency not in graph
        }
        self.assertEqual(missing, set())

    def test_full_graph_is_acyclic(self) -> None:
        graph = release_graph()
        visiting: list[tuple[str, str]] = []
        visited: set[tuple[str, str]] = set()

        def visit(node: tuple[str, str]) -> None:
            if node in visiting:
                cycle = visiting[visiting.index(node) :] + [node]
                self.fail(f"HelmRelease dependency cycle: {cycle}")
            if node in visited:
                return
            visiting.append(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.pop()
            visited.add(node)

        for release in graph:
            visit(release)

    def test_infrastructure_does_not_depend_on_applications(self) -> None:
        graph = release_graph()
        infrastructure = stage_releases(INFRASTRUCTURE_STAGE)
        applications = stage_releases(APPLICATION_STAGE)
        self.assertFalse(infrastructure & applications)

        def application_dependencies(node: tuple[str, str]) -> set[tuple[str, str]]:
            dependencies: set[tuple[str, str]] = set()
            for dependency in graph[node]:
                if dependency in applications:
                    dependencies.add(dependency)
                dependencies.update(application_dependencies(dependency))
            return dependencies

        reverse_dependencies = {
            release: application_dependencies(release)
            for release in infrastructure
            if application_dependencies(release)
        }
        self.assertEqual(reverse_dependencies, {})

    def test_studio_shared_is_uniquely_infrastructure_owned(self) -> None:
        studio_shared = ("studio-shared", "frontend-studio")
        self.assertIn(studio_shared, stage_releases(INFRASTRUCTURE_STAGE))
        self.assertNotIn(studio_shared, stage_releases(APPLICATION_STAGE))

    def test_opensearch_waits_for_its_public_ca_bundle(self) -> None:
        graph = release_graph()
        self.assertIn(
            ("trust-manager", "infra-trust-manager"),
            graph[("opensearch", "monitor-opensearch")],
        )

    def test_kube_prometheus_stack_waits_for_rook(self) -> None:
        graph = release_graph()
        self.assertIn(
            ("rook-ceph", "infra-rook-ceph"),
            graph[("kube-prometheus-stack", "monitor-kube-prometheus-stack")],
        )

    def test_all_releases_retry_install_and_upgrade(self) -> None:
        slower_retries = {
            "librechat-code-interpreter-package-init",
            "librechat-code-interpreter-worker",
        }
        for path in ROOT.glob("releases/**/*.yaml"):
            for document in re.split(r"(?m)^---\s*$", path.read_text(encoding="utf-8")):
                if not re.search(r"(?m)^kind: HelmRelease$", document):
                    continue
                name_match = re.search(r"(?m)^  name: ([^\n]+)$", document)
                self.assertIsNotNone(name_match, str(path))
                name = name_match.group(1) if name_match else ""
                interval = "2m" if name in slower_retries else "1m"
                for operation in ("install", "upgrade"):
                    block = re.search(
                        rf"(?ms)^  {operation}:\n(.*?)(?=^  [a-zA-Z]|\Z)", document
                    )
                    self.assertIsNotNone(block, f"{path}: {name} {operation}")
                    self.assertIn(
                        "    strategy:\n"
                        "      name: RetryOnFailure\n"
                        f"      retryInterval: {interval}",
                        block.group(0) if block else "",
                        f"{path}: {name} {operation}",
                    )

    def test_dify_agentgateway_oidc_uses_client_keycloak_values(self) -> None:
        release = (ROOT / "releases/keycloak/oidc-dify-agentgateway.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("name: client-values", release)
        self.assertIn("name: keycloak-product-values", release)

    def test_openbao_uses_poller_wait_strategy(self) -> None:
        release = RELEASES["openbao"].read_text(encoding="utf-8")
        self.assertIn("waitStrategy:\n    name: poller", release)

    def test_internal_certificate_consumers_wait_for_internal_issuer(self) -> None:
        graph = release_graph()
        internal_issuer = ("cert-manager-internal-issuer", "infra-cert-manager")
        public_issuers = ("cert-manager-issuers", "infra-cert-manager")

        for release in (
            ("pii-engine", "monitor-pii-engine"),
            ("opensearch", "monitor-opensearch"),
        ):
            with self.subTest(release=release):
                self.assertIn(internal_issuer, graph[release])
                self.assertNotIn(public_issuers, graph[release])

    def test_pii_engine_waits_for_its_openbao_trust_bundle(self) -> None:
        graph = release_graph()
        self.assertIn(
            ("trust-manager", "infra-trust-manager"),
            graph[("pii-engine", "monitor-pii-engine")],
        )

    def test_code_interpreter_package_init_gates_the_worker(self) -> None:
        graph = release_graph()
        namespace = "librechat-code-interpreter"
        package_init = ("librechat-code-interpreter-package-init", namespace)
        self.assertEqual(
            graph[package_init],
            [
                ("librechat-code-interpreter-shared", namespace),
                ("librechat-code-interpreter-valkey", namespace),
            ],
        )
        self.assertIn(
            package_init,
            graph[("librechat-code-interpreter-worker", namespace)],
        )

    def test_optional_librechat_packages_do_not_gate_the_default_stage(self) -> None:
        graph = release_graph()
        applications = stage_releases(APPLICATION_STAGE)
        optional = {
            ("frontend-librechat-rag-api", "frontend-librechat"),
            ("librechat-code-interpreter-api", "librechat-code-interpreter"),
        }

        self.assertTrue(optional.isdisjoint(applications))
        self.assertTrue(optional.isdisjoint(graph[("librechat", "frontend-librechat")]))

    def test_code_interpreter_package_hook_is_retry_safe(self) -> None:
        job = (
            ROOT
            / "charts/librechat/code-interpreter/package-init/templates/package-init-job.yaml"
        ).read_text(encoding="utf-8")
        package_release = (
            ROOT / "releases/librechat/code-interpreter/package-init.yaml"
        ).read_text(encoding="utf-8")
        shared_release = (
            ROOT / "releases/librechat/code-interpreter/shared.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn('"helm.sh/hook": pre-install,pre-upgrade', job)
        self.assertIn(
            '"helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded',
            job,
        )
        self.assertNotIn("hook-failed", job)
        self.assertNotIn("ttlSecondsAfterFinished", job)
        self.assertEqual(package_release.count("name: RetryOnFailure"), 2)
        self.assertNotIn("disableHooks: true", package_release)
        self.assertEqual(shared_release.count("disableWait: true"), 2)

    def test_opensearch_security_bootstrap_hook_is_retry_safe(self) -> None:
        job = (ROOT / "charts/opensearch/templates/init-security-bootstrap.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn('"helm.sh/hook": post-install,post-upgrade', job)
        self.assertIn('"helm.sh/hook-weight": "-5"', job)
        self.assertIn('"helm.sh/hook-delete-policy": before-hook-creation', job)
        self.assertNotIn("hook-failed", job)

    def test_trust_manager_waits_for_bundle_sync(self) -> None:
        release = TRUST_MANAGER_RELEASE.read_text(encoding="utf-8")
        self.assertIn("apiVersion: trust.cert-manager.io/v1alpha1", release)
        self.assertIn("kind: Bundle", release)
        self.assertRegex(
            release,
            r"current: .*e\.type == 'Synced'.*e\.status == 'True'",
        )

    def test_oidc_clients_precede_their_frontends(self) -> None:
        graph = release_graph()
        expected_edges = {
            ("librechat", "frontend-librechat"): (
                "keycloak-librechat-oidc",
                "auth-keycloak",
            ),
            ("studio-api", "frontend-studio"): (
                "keycloak-studio-oidc",
                "auth-keycloak",
            ),
            ("studio", "frontend-studio"): (
                "keycloak-studio-oidc",
                "auth-keycloak",
            ),
        }

        for release, dependency in expected_edges.items():
            with self.subTest(release=release):
                self.assertIn(dependency, graph[release])


if __name__ == "__main__":
    unittest.main()
