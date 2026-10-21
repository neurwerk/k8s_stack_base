# Development

## Local validation

Run commands from the Base checkout root. Install the tools pinned in [`.tool-versions`](../../.tool-versions) with Mise, and provide Python meeting [`pyproject.toml`](../../pyproject.toml). Node is needed by provisioning-runner tests; Lua is needed by filter tests. Python dependencies are resolved through the committed `uv.lock`.

```bash
mise install
mise exec -- make check
```

The [Makefile](../../Makefile) runs these checks:

| Check | Scope |
| --- | --- |
| `tools`, `deps-verify` | Required executables and committed Helm dependencies, without updating locks or archives. |
| `helm-lint`, `rendered-check` | Strict chart linting, rendering, Kubernetes schema validation, and kube-linter. |
| `kustomize-validate` | Builds and schema-checks the package roots listed in the target. |
| `chart-check` | Chart-specific rendered behavior and helper tests. |
| `security-check` | Offline tests of live-runner access guards. |
| `platform-check` | Generated release inventory and release-evidence tests. |

Rendering uses [`tests/validation/helm-lint-values.yaml`](../../tests/validation/helm-lint-values.yaml), not a client's production settings. Schema validation skips missing schemas, so a successful run does not validate every custom resource field. Live gateway and PostgreSQL acceptance are separate opt-in targets; `make check` does not invoke them.

CI also checks the Python lockfile, release prose, and the lightweight hooks in [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml). See the [validation workflow](../../.github/workflows/validate.yaml) for tool setup and the conditional isolated WireGuard packet check.

## Changing packages

Keep shared deployment changes in the owning product's chart and release package. Keep application logic in its component repository and client-specific settings in the client repository. Preserve Kubernetes resource names unless the change includes an approved migration. The shared [deployment conventions](https://github.com/neurwerk/docs/blob/main/dev/conventions/deployment.md) cover values precedence, schema expectations, chart versioning, and dependency archives.

For an intentional chart dependency update, review `Chart.yaml`, `Chart.lock`, and the packaged archives together. Ordinary validation must not regenerate them. `make helm-deps` rebuilds archives from committed locks and is a preparation command, not a validation fix.

## Release evidence

[`scripts/platform_release.py`](../../scripts/platform_release.py) builds the manifest from release configuration and the chart, HelmRelease, image, and toolchain inventories. After an approved package or image change, regenerate and review the resulting inventory:

```bash
mise exec -- make release-manifest
mise exec -- make check
mise exec -- make release-check
```

Release preparation also needs consistent `VERSION`, release configuration, changelog, migration guidance, and Git provenance. Use the shared [image release procedure](https://github.com/neurwerk/docs/blob/main/dev/operations/image-releases.md) and [platform-release CLI reference](https://github.com/neurwerk/k8s_stack_tooling/blob/main/cli_tools/platform_release/README.md) for publication mechanics. Local validation does not publish a release or select it for a client.
