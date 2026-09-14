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

The optional `make streaming-acceptance` regression uses an explicitly supplied,
checksum-verified AgentGateway binary and synthetic local servers. See
[streaming test setup](tests/live/agentgateway/README.md) for installation and
execution. The binary is not required by `make check`.
The separate [workaround note](https://github.com/neurwerk/documentation/blob/main/dev/operations/agentgateway-streaming-workaround.md)
explains the auth-header change, upstream issues, and adoption gates.

## Application Access Plans

`scripts/check_application_access.py` is an **offline, planning-only** validator.
It reads one explicit normalized YAML plan, not a client repository, Helm values,
Flux output, or Kubernetes resource. A human must derive its endpoint selection,
effective logical endpoints, feature flags, and device grants from reviewed
effective values, or use the bounded local composition adapter described below.
Success says **"Access plan valid; planning only. Runtime enforcement/DNS not
verified."** It does not prove actual client selection, topology, authentication,
certificate issuance/trust, DNS, or reachability, and does not enforce access.
It never fetches, discovers resources, reads referenced Secrets, or rewrites input.
Do not supply secrets or credentials, including in device IDs.

Run from this repository with pinned tools and cached Python dependencies:

```bash
mise exec -- uv run --offline --frozen python scripts/check_application_access.py tests/platform/application-access.example.yaml
```

The [synthetic example](tests/platform/application-access.example.yaml) is not
runtime configuration. The exact input contract is:

| Field | Required value |
| --- | --- |
| `access.boundary` | `internet` or `client-network` |
| `access.default` | `internal`, `public`, or `restricted`; only supplies omitted endpoint levels |
| `certificates.profile` | `public-production` only; `private-ca` and staging profiles are unsupported in this slice |
| `canonicalEndpointRouting.mode` | `internal-traefik` or `public-dns`, independent of access and certificate choices |
| `endpoints` | Mapping of explicitly selected known endpoint IDs to endpoint mappings; `{}` selects nothing |
| `endpoints.<id>.level` | Optional `internal`, `public`, or `restricted`; inherits `access.default` |
| `endpoints.<id>.devices` | Required only for effective `restricted` level; list of unique, nonempty, unpadded device ID strings |
| `endpoints.librechat.features.files` | Explicit boolean required whenever `librechat` is present |
| `endpoints.dify.features.consoleSSO` | Explicit boolean required whenever `dify` is present |

All four top-level mappings and their listed global fields are required. All
unknown keys, incorrect types, nulls, duplicate YAML keys, YAML aliases/merge keys,
and explicit YAML tags are rejected. Endpoint mappings allow only `level`,
`devices` when restricted, and the exact required `features` mapping for LibreChat
or Dify. Other endpoints do not accept `features`. There is no `enabled` field:
omission means not selected, and defaults never enable an endpoint. An inherited
`restricted` level still requires that endpoint's own explicit device list.

The small platform-owned browser/native-client catalog is defined in the script:

| Known endpoint | Required human-client dependency |
| --- | --- |
| `keycloak` | None |
| `librechat` | `keycloak`; also `librechat-files` when `features.files: true` |
| `librechat-admin` | `librechat` and `keycloak` |
| `librechat-files` | None; the logical effective browser file endpoint, not necessarily the default RGW backend |
| `studio` | `keycloak` |
| `dify` | `keycloak` only when `features.consoleSSO: true` |
| `langfuse` | None |
| `agentgateway` | None |
| `forgejo` | `keycloak` |

Same-origin UI, API, callback, Git-over-HTTPS, and LFS paths belong to one endpoint,
not independent access selections. Forgejo SSH and backend service/model calls
are outside this catalog. There is no inferred Langfuse-to-Keycloak dependency.
Private services such as databases, RAG, and Code Interpreter never become
automatic endpoints. The file endpoint entry represents the effective browser
destination even if file storage is overridden; this tool cannot verify that
mapping or whether two logical endpoint entries actually share an origin.

Every active dependency must be explicitly present, even for a deny-all source.
Dependency checks are directional and apply at every edge of the catalog:

| Source level | Allowed target level/grants |
| --- | --- |
| `public` | `public` only |
| `internal` | `internal` or `public`, never `restricted` |
| `restricted`, internet boundary | `public`, or `restricted` with every source device ID admitted by target; `internal` is not assumed reachable by off-network devices |
| `restricted`, client-network boundary | `internal`, or `restricted` with every source device ID admitted by target |

Under `client-network`, every `public` endpoint and a `public` default are invalid,
even if all endpoints override that default. The boundary is an abstract approved
network, not a CIDR or LAN discovery promise. Network facts and enforcement are
deferred. `restricted` grants compare exact, case-sensitive device IDs, not group
names, list lengths, or assumed group membership. Supply actual expanded grant
sets, not group labels; device registration and identity authenticity are not
verified here. An empty list means deny-all, never allow-all, and prints a
per-endpoint `WARNING: ... DENY-ALL; no admitted devices` even on a valid plan.

The CLI exits `0` for a valid plan (possibly with deny-all warnings), `1` for input
or dependency errors, and `2` for command-line usage errors. Warnings/errors go to
stderr; success goes to stdout. Diagnostics name known schema paths or dependency
edges and mismatches without printing supplied values or YAML parser excerpts.
The Python API `load_plan(Path)` safely loads the file; `validate_plan(plan)`
returns `(errors, warnings)` without mutating input and raises `PlanError` for
malformed structure. Tests run automatically through `make platform-check` and
`make check`. No charts, release contracts, runtime defaults, or client values
consume this plan format.

### Local Composition Adapter

`scripts/check_client_application_access.py` derives that normalized plan from
two supplied local checkouts. It is **offline planning, not runtime enforcement**.
It never fetches repositories, runs Helm/Kustomize, renders Secrets, resolves Secret
references, changes inputs, or contacts Kubernetes, DNS, OpenBao, or applications.

```bash
mise exec -- uv run --offline --frozen python scripts/check_client_application_access.py \
  --client-root /path/to/example-client --platform-root /path/to/platform \
  --cluster prod-eu-1
```

The client policy is always `config/application-access.yaml`. Its entire schema
is the following, using the same boundary, level, and expanded device-ID rules as
the normalized checker:

```yaml
access:
  boundary: internet
  default: internal
endpoints:
  keycloak: {}
  forgejo:
    level: restricted
    devices: [device-a]
```

This is a schema illustration, not an inventory for a real client. The endpoint
keys must **exactly** match the derived selection. Entries allow only optional
`level` and `devices`. Features, certificates, routing, hostnames, and enablement
must not be duplicated in policy. Unknown keys, duplicate keys, aliases, merge
keys, explicit tags, and unsupported types fail with value-free diagnostics.

Supported input contract:

- Start at `clusters/<cluster>/kustomization.yaml`; follow only its selected local
  resource files/directories and selected Flux Kustomizations. The `k8s-stack`
  source maps to the supplied platform root and `flux-system` to the client root.
  Bootstrap self-selection is recognized without traversing it twice. Separate
  Forgejo stages remain selected even with their external Gateway disabled.
- Local Kustomizations support `resources`, `namespace`, harmless common labels
  and annotations, and stable-name `configMapGenerator.files` entries, including
  `key=relative-file`. Generated file contents are loaded only when referenced as
  values. ConfigMap identity is namespace-local; duplicate identities fail.
- Management annotations cannot suppress application of selected facts. Selected
  resource annotations, `commonAnnotations`, and generator annotations permit
  only absent or `Override` SSA policy and absent or `enabled` reconciliation.
  `kustomize.toolkit.fluxcd.io/ssa` values such as `Ignore`, `IfNotPresent`, and
  `Merge`, and `kustomize.toolkit.fluxcd.io/reconcile: disabled`, are rejected
  before ConfigMaps or ExternalSecret producers can supply facts. Unsupported
  annotations at any selected layer fail even if a later transform might replace
  them; the adapter does not infer whether stale runtime objects were applied.
- Chart defaults precede each HelmRelease's actual ordered `valuesFrom`, then
  inline `spec.values`. No fixed shared/client/product ordering is assumed.
  Required missing ConfigMaps fail. Missing optional ConfigMaps and unscoped
  Secret references make relevant leaves unknown. A later explicit non-secret
  scalar settles that leaf, not its missing siblings. Secret contents are never
  inspected, even when a similarly named local file exists.
- A single selected namespace-local ExternalSecret producer can establish a
  Secret's finite declared write scope. Its exact target must use explicit
  `creationPolicy: Owner`, `engineVersion: v2`, and `mergePolicy: Replace`
  (including the default). Its template data must contain the referenced
  values key, with static nested mapping keys. Optional sibling Secret output
  keys must be static valid Kubernetes data keys (1-253 ASCII letters, digits,
  `-`, `_`, or `.`, excluding `.` and names beginning with `..`), with only raw
  full-field `{{ .identifier }}` expressions.
  These siblings support direct workload consumers and do not contribute Helm
  writes; only the referenced YAML is inspected. Only whole-scalar
  `{{ .identifier | quote }}` placeholders are replaced by parser markers;
  all resulting leaf values, including literal values, remain **unknown**.
  No upstream data or remote references are resolved. Missing, ambiguous or
  unsupported producers leave the entire reference unknown. Merge policies,
  templateFrom, other sibling output forms, sequences, aliases, dynamic keys and other
  Go syntax/interpolation are unsupported. This is a declared producer contract,
  **not proof of actual synchronization, contents, ownership or tamper resistance**.
- All Secret `targetPath` references are rejected, including apparently disjoint
  dotted paths. An opaque Helm strvals payload can contain comma assignments that
  overwrite sibling values, with precedence over inline values. A path alone
  cannot establish write scope. ConfigMap `targetPath` and Helm `--set` parsing
  also remain unsupported; producer-shape support applies only to root YAML refs.
- The fixed endpoint bundles cover Keycloak server, LibreChat app/shared, Admin
  Panel, Studio web/API, Dify web/API/shared, Langfuse, AgentGateway, and enabled
  selected Forgejo. Rook's selected external object route requires file-endpoint
  classification even without LibreChat object storage. Unknown charts fail;
  the explicit `NON_ENDPOINT_CHARTS` catalog excludes backend, controller,
  operator-only and job packages, not arbitrary serving charts.
- Only the fixed chart surfaces described here are modeled, not arbitrary vendor
  extensions. Enabled alternate ingresses fail classification: nested Langfuse
  ingress, Grafana/Prometheus/Alertmanager ingresses, OpenSearch ingress, and
  OpenBao server ingress/OpenShift route/HTTPRoute/TLSRoute. The two fixed
  Langfuse and kube-prometheus-stack dependency archives supply their actual
  upstream defaults before wrapper defaults; their committed bytes are verified
  and only their regular `values.yaml` member is parsed, without extraction or
  rendering. This is not a generic Helm dependency or exposure interpreter.
- Raw namespaces, ConfigMaps, RBAC, service accounts, NetworkPolicies and External
  Secrets resources are non-endpoint inputs. Deployments, Services, CRDs and
  ResourceQuotas are accepted only in the exact reviewed generated Flux v2.9.4
  controller bundle, SHA-256
  `97da4654bc11de5637d7f506b0a73707e1e6a1f7a8e9fa8e307063c0b9befa1f`.
  Filename and controller resource names do not establish trust. Changed/unknown
  bundles and namespace transforms of controller workloads fail. Other raw
  serving resources remain unsupported. Updating Flux requires reviewing and
  deliberately updating this bounded bootstrap contract.
- LibreChat app/shared file flags must agree. The app's file endpoint must use
  its canonical RGW hostname; an override to another origin, non-root storage
  path, or virtual-hosted bucket addressing fails. A selected Rook route is
  compared independently. Dify SSO comes from the API chart's exact string
  boolean `ENABLE_SOCIAL_OAUTH_LOGIN`, never Python truthiness.
- Advertised origins, enabled OIDC callback origins and Studio's OIDC authority
  must agree with canonical endpoints. The Admin Panel uses the main LibreChat
  callback but its own web origin. Callback paths must exactly match
  `/oauth/openid/callback` (LibreChat), `/api/admin/oauth/openid/callback` (Admin
  Panel), `/auth/callback` (Studio), and `/console/api/oauth/authorize/keycloak`
  (Dify when console SSO is enabled). Web origins must have no path. Forgejo's
  fixed `/user/oauth2/keycloak/callback` is constructed by its selected chart
  from the checked canonical hostname, not an independently configurable URL.
  Each enabled callback registration's Keycloak hostname and realm must match
  the selected issuer and Keycloak, including the separate Forgejo OIDC release.
  AgentGateway's empty redirect/web-origin pair is a native/service-only
  registration and creates no Keycloak browser dependency. Any nonempty pair or
  one-sided callback configuration is rejected as unsupported browser mode; no
  conditional dependency is silently omitted. Unsupported callback forms (including
  local HTTP callbacks) fail rather than inventing an endpoint. Same-origin logical
  endpoints require identical levels and expanded grants. Gateway ports other
  than canonical HTTPS 443 are unsupported. Gateway enablement is not exposure.
- Certificate profiles derive from effective `publicCertificates.useProduction`;
  only production is supported. Canonical routing observations must agree and
  remain independent of access. No observed mode means unsupported, not a guessed
  default. This is not a general Helm/chart semantic validator or readiness check.

Forgejo OIDC consumes quoted `values.yaml` from `forgejo-oidc-values`, without
`targetPath`. The same producer retains the raw `oidcClientSecret` output because
the registration Job reads that key directly; the Helm value is only a rotation
trigger and never appears in workload manifests. Both outputs use the same
existing source field, Secret identity, ownership, retention, and Flux watch label.
The authorized early-alpha change updates producer and consumer together and
accepts a temporary missing-key reconciliation failure until ESO synchronizes.
Verify current-generation producer/consumer readiness and application health
afterward, without printing Secret values. Stable clients stay on their selected
release until a later reviewed release bump; no credential generation or OpenBao
schema change is needed.

Remote paths, root/symlink escapes, resource/Flux cycles, suspension, patches,
components, substitutions/postBuild, decryption, plugins, post-renderers, alternate
chart sources/values files, and alternate target namespaces fail closed. The
adapter also rejects source ignore files, runtime `preserveValues`, and disabled
Helm hooks rather than interpreting a runtime state it cannot inspect. The
supported subset deliberately rejects unresolved or unclassified inputs instead
of treating them as absent or safe. No runtime edit follows from a failure.

The selected platform `GitRepository/flux-system/k8s-stack` must use exactly
`https://github.com/neurwerk/k8s_stack_base.git` and name local `main` or an exact
stable `vX.Y.Z` tag. HEAD must equal the selected local branch/tag's resolved
commit. Tags are never reinterpreted as candidate inputs. There is no fetch,
remote-freshness claim, or signature check.

Git inspection uses only `rev-parse`, `ls-tree`, and names-only `ls-files`.
It never uses `status`, `diff`, content conversion, textconv, or clean/process
filters. Lazy fetching and replacement objects are disabled explicitly; the
inherited Git environment is scrubbed, global/system configuration is disabled,
all transport protocols are denied, and fsmonitor/hooks/automatic maintenance
are disabled. Git must support `--no-lazy-fetch`; unsupported versions fail
instead of falling back to less constrained inspection.

Snapshot state comes from raw bytes and executable modes compared with committed
blob identities, plus tracked/untracked filename inventory. Ignored caches are
not read for snapshot reporting. Consumed client input paths are tracked, including
the policy and lazily loaded ConfigMap files, before revision reporting. A consumed
client file absent from HEAD or differing from its committed blob forces
`clientSnapshot: modified` even when Git ignores it. Such client candidates remain
allowed; an unrelated ignored cache does not change the report. Independently,
**every consumed platform file**
must be a regular committed blob with matching raw bytes and mode, including
manifests, generated values inputs, chart defaults, and the fixed vendor archives.
Selected ignored/untracked payloads and symlinks are rejected even when the rest
of the checkout appears unchanged. No index stat-cache or filter result grants
provenance. Snapshot reporting describes worktree bytes, not staged-only changes.

Output distinguishes runtime platform/client revisions from the checker revision
and marks modified client/checker snapshots. Modified client inputs are planning
candidates, not a claim that HEAD contains the policy. These constraints prevent
repository attribute commands from executing; they are not an OS sandbox against
a hostile Git binary, filesystem, or concurrent privileged modification.

The importable API is
`scripts.check_client_application_access.derive_plan(client_root: Path, platform_root: Path, cluster="prod-eu-1")`, returning
`(normalized_plan, revision_identities)` and raising `PlanError` on invalid or
unsupported input. It calls `validate_plan(normalized_plan)` before returning.
The CLI exits 0 for valid plans, 1 for input/dependency failures and 2 for usage
errors. It reports known endpoint IDs, safe schema diagnostics and revisions,
never raw hostnames, grants or YAML. All results include the explicit offline
planning/no runtime enforcement disclaimer. Synthetic tests include actual Base
release composition with generic example values; no private client fixtures are
stored in this repository.

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
request. Successors move the authored `Unreleased` body into the dated version
entry, preserving optional subsections and fenced content and leaving Unreleased
empty. If that body is empty, the required nonempty, single-line summary becomes
one bullet. Existing versioned content is preserved. Selected TODO markers block
preparation or release validation; no successor TODO placeholders are generated.
The one-time
`bootstrap-v0.1.0` mode is valid only from the unpublished `0.0.0` baseline when
the repository has zero tags. It records complete reachable history, allows only
fresh installation, and has no predecessor. Every platform release supports
installation into a verified empty or replacement environment; this invariant is
not a release-preparation input. Every successor must name the latest signed
release matching the pre-prepare `VERSION` and records only the commits after
that predecessor. Successor compatibility defaults stable upgrades to
`supported`; use `fresh-install-only` when no stable in-place upgrade is safe. It
may also list exact full lowercase alpha source commits when a reviewed forward
alpha-to-stable migration is supported. Downgrade is currently
always recorded as unsupported; enabling it requires a coordinated schema,
validation, preparation, and client change. Restrict environment deployment
branches to the default branch without requiring a deployment reviewer. Manual
dispatch authorizes draft preparation only; it does not authorize tagging,
publication, adoption, or deployment.

Public release notes default to `## vX.Y.Z`, a blank line, and only the selected
changelog body. Add optional `###` sections for special instructions, breaking
changes, or required actions when relevant; empty sections and absence claims
are not required or synthesized. Publication renders the exact signed snapshot
with trusted default-branch tooling, without appending migrations or GitHub PR
history. The `notes --generated-notes PATH` CLI option remains explicit opt-in.

`migration_scaffold(version, stable_upgrade, alpha_revisions, recovery)` generates
deterministic compact machine-readable Support and Recovery declarations plus
the required nonempty Breaking Changes reference to `CHANGELOG.md`. This retains
the client-consumed schema and parser, not proof of tested upgrades or recovery.
Review the actual transition evidence and add needed instructions to the
changelog. Historical signed release files and legacy compatibility parsing are
unchanged; the one-time bootstrap still requires its reviewed migration evidence.

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
