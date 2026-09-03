# Neurwerk Kubernetes Platform

This repository is the public, versioned Kubernetes platform contract for the
Neurwerk stack. It contains owned Helm wrapper charts, reviewed upstream chart
dependencies, Flux `HelmRelease` definitions, platform namespaces and defaults,
and offline validation tooling. It does not contain a complete cluster
configuration, client secrets, or application source code.

The current platform version is recorded in [`VERSION`](VERSION). Production
consumers must select an exact, signed `vX.Y.Z` tag rather than `main`, a branch,
or a SemVer range.

## Architecture And Ownership

The central ownership boundary is:

```text
chart source != platform release contract != client values != cluster composition
```

This repository owns:

- `charts/<product>/`: chart source and environment-independent defaults;
- `releases/<product>/`: platform `HelmRelease` contracts and product defaults;
- `releases/shared/`: platform-wide defaults, never customer facts;
- `releases/namespaces/`: platform namespace objects;
- `releases/infrastructure/` and `releases/applications/`: aggregation only;
- `release/`: versioned manifest, compatibility, migration, and public trust
  metadata;
- `tests/`, `scripts/`, and `Makefile`: platform validation.

Client repositories own non-secret customer facts, product values, ConfigMap
generation, and the Flux cluster graph. Service repositories own application
behavior and images. Operator tooling owns privileged initialization. OpenBao is
the runtime source for credentials; no real Secret manifest, private key,
provider token, or recovery material belongs here.

This repository is not a standalone installer. A client-owned Flux composition
combines an independently trusted platform tag with namespace-local client
values and secrets.

## Repository Layout

```text
charts/                         Helm chart source and vendored dependencies
releases/<product>/             product release contracts
releases/{namespaces,infrastructure,applications}/
                                ordered package indexes
releases/shared/                platform defaults
release/config.yaml             reviewed release inputs
release/manifest.yaml           generated platform bill of materials
release/migrations/             version-specific operator notes
release/trust/                  public release verification key metadata
scripts/platform_release.py     release generation and consistency checks
tests/                          rendered, security, and release-contract tests
```

## Prerequisites

Local validation requires a Unix-like shell, Git, GNU Make, and the versions in
[`.tool-versions`](.tool-versions):

| Tool | Version |
| --- | --- |
| Python | 3.12 or newer |
| uv | 0.11.21 |
| Helm | 4.2.4 |
| kustomize | 5.8.1 |
| kubeconform | 0.8.0 |
| kube-linter | 0.8.3 |
| pre-commit | 4.6.1 |

`mise install` or an equivalent tool manager can install the declared CLI
versions. The first `uv` run may require network access to populate its cache.
Helm dependencies are already committed and validation does not update them.

Deployments additionally require Kubernetes, Flux, CRDs, storage, DNS,
certificates, OpenBao initialization, and published images matching the selected
tag's `release/manifest.yaml`. The exact prerequisite versions and compatibility
limits are release-specific; do not infer upgrade safety from SemVer alone.

## Validation

Run the complete local validation suite from the repository root:

```bash
uv sync --frozen
make check
pre-commit run --all-files
```

`make check` verifies tool availability and Helm dependency locks, lints and
renders every chart, validates the root Kustomizations with kubeconform, runs
kube-linter, and executes chart, static security, and platform contract tests.
It does not contact or mutate a cluster.

Focused read-only checks are:

```bash
make deps-verify
make helm-lint
make helm-validate
make kustomize-validate
make kube-linter
make chart-check
make security-check
make platform-check
make release-check
```

`make helm-deps` and `make release-manifest` modify committed artifacts and are
not validation-only commands. Live acceptance targets are explicitly opted in
and are not part of `make check`.

## Verify A Release

Release tags are annotated SSH-signed tags. The approved signer contract is:

```text
identity:    platform-release
algorithm:   ssh-ed25519
fingerprint: SHA256:+rDcofrsfRE3ElJJxnUVoB3gmoEzZJUrisDqLZMHimw
```

Trust bootstrap warning: the public key committed in this repository is useful
verification material, but it cannot establish its own initial authenticity.
Obtain the expected fingerprint through an independent, operator-controlled
channel before trusting the repository, a release page, CI output, or a cluster
Secret. If the out-of-band value differs, stop.

After independently authenticating the fingerprint and after the first release
has been published:

```bash
ssh-keygen -lf release/trust/platform-release.sshpub -E sha256
awk '{print "platform-release namespaces=\"git\" " $1 " " $2}' \
  release/trust/platform-release.sshpub > allowed_signers
git fetch origin tag v0.1.0
git -c gpg.format=ssh \
  -c gpg.ssh.allowedSignersFile="$PWD/allowed_signers" \
  verify-tag v0.1.0
git show v0.1.0:VERSION
rm allowed_signers
```

Replace `v0.1.0` with the intended exact tag and confirm `VERSION` matches it
without the leading `v`. Do not create a Flux trust Secret from repository key
material until the fingerprint has been authenticated independently.

## Release Automation Trust

Configure `PLATFORM_RELEASE_ALLOWED_SIGNER` as a repository or organization
Actions variable, after out-of-band fingerprint authentication, using this
single OpenSSH allowed-signers line:

```text
platform-release namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOaoKMNPBk8+i23jqEmS7rwXso1HjEoe+8iDIXiJkLeD
```

The read-only tag workflow verifies the signed tag with tooling checked out from
the default branch, then runs the tag's complete validation without write
permission. A separate default-branch `workflow_run` independently verifies the
tag workflow matches its trusted default-branch definition, then verifies the
signer, provenance boundary, release contract, and default-branch ancestry before
entering the write-capable `platform-release` environment. It fails closed when
the variable is absent, malformed, inconsistent with the trusted default-branch
public key, or not the documented fingerprint. The environment contains no
configuration variables or credentials and does not require a deployment
reviewer. Successful verified publication authorizes the GitHub Release only;
it does not authorize client adoption or deployment.

The environment is not a substitute for repository rules. An active tag ruleset
must restrict creation, update, and deletion of `v*` tags to release custodians.
Tags are immutable; signer rotation requires an explicit trust transition and a
new release, never moving an existing tag.

## Release Preparation

The manual `Prepare Release PR` workflow uses the protected
`release-preparation` environment. Store only `RELEASE_AUTOMATION_APP_ID` and
`RELEASE_AUTOMATION_APP_PRIVATE_KEY` there as environment secrets for a dedicated
GitHub App installed only on this repository with contents and pull-request write
permissions. The workflow's `GITHUB_TOKEN` remains read-only, while App-authored
pull requests trigger normal pull-request checks.

Preparation may write only the version, changelog, release configuration,
generated manifest, and version-specific migration document to a draft pull
request. Generated prose contains deliberate `TODO` markers. The one-time
`bootstrap-v0.1.0` mode is valid only from the unpublished `0.0.0` baseline when
the repository has zero tags. It records complete reachable history, declares no
upgrade sources, and has no predecessor. Every platform release supports
installation into a verified empty or replacement environment; this invariant is
not a release-preparation input. Every successor must name the latest signed
release matching the pre-prepare `VERSION` and records only the commits after
that predecessor. Successor compatibility may
also list exact full lowercase alpha source commits when a reviewed forward
alpha-to-stable migration is supported. Downgrade is currently
always recorded as unsupported; enabling it requires a coordinated schema,
validation, preparation, and client change. Restrict environment deployment
branches to the default branch without requiring a deployment reviewer. Manual
dispatch authorizes draft preparation only; it does not authorize tagging,
publication, adoption, or deployment.

## Client Adoption Proposals

Set `CLIENT_ADOPTION_ENABLED` and `CLIENT_ADOPTION_REPOSITORIES` as repository or
organization Actions variables. The repository list is a JSON array of objects
containing an exact repository and its name, for example
`[{"repository":"OWNER/CLIENT_REPOSITORY","name":"CLIENT_REPOSITORY"}]`.
These values are evaluated in the job condition and matrix before an environment
is attached, so they must not be environment variables. Keep
`PLATFORM_RELEASE_ALLOWED_SIGNER` at repository or organization scope as well.

The `client-adoption` environment is only a branch and credential boundary and
does not require a deployment reviewer.
Store `CLIENT_ADOPTION_APP_ID` and `CLIENT_ADOPTION_APP_PRIVATE_KEY` there for a
dedicated GitHub App installed only on approved client repositories with contents
and pull-request write permissions. No customer repository fact is committed
here.

Adoption downloads the exact tag artifact from successful default-branch
publication, checks out default-branch tooling separately, and treats the signed
tag checkout only as data. It rejects releases older than `v0.1.0` with an
explicit provenance-contract message, re-verifies the tag and main ancestry,
scopes each installation token to one matrix repository, changes only
`clusters/prod-eu-1/platform-source.yaml`, and opens a draft pull request. It
never merges, deploys, or reconciles a cluster. Successful publication or an
authorized manual dispatch may create the draft; maintainer merge of the exact
reviewed client change is the adoption authorization.

## Initialization Defaults

Langfuse's default organization, project, and display names are generic
initialization placeholders. Client-owned values should set the intended
non-secret identity before first initialization. The unpublished imported
baseline also retains the historical `admin@org.com` fallback. Do not treat the
fallback as an operational account. Replacing it and moving generic identities
into client-owned values requires coordinated client changes and a later
release.

## Workflow Supply Chain

All third-party GitHub Actions are pinned to full commit SHAs. In particular,
the `supplypike/setup-bin` pin resolves to upstream `v4.0.1`, and the
`pre-commit/action` pin resolves to upstream `v3.0.1`. Keep updates explicit and
review the exact upstream commit before changing any workflow pin.

## Support And Security

- Read [CONTRIBUTING.md](.github/CONTRIBUTING.md) before proposing a change.
- Use [GitHub Discussions](https://github.com/neurwerk/k8s_stack_base/discussions)
  for questions and support.
- Use [GitHub Issues](https://github.com/neurwerk/k8s_stack_base/issues) for
  reproducible bugs and scoped feature requests.
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
- See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for vendored chart
  provenance and licensing.

Owned content is licensed under the [MIT License](LICENSE). Third-party content
retains its upstream license.
