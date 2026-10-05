# Optional shared PostgreSQL add-on provisioner

Each optional product owns a separate `HelmRelease` for this chart, in
`infra-postgres-operations`, with a distinct release name, role, and database
names. It depends on `postgres-operations` and is **not** installed by Base's
default stage. For example, an add-on may pass these non-secret values:

```yaml
addon:
  role: example
  passwordSecret: example-postgres-values
  databases:
    - name: example_data
      vector: false
    - name: example_vectors
      vector: true
```

The add-on's approved credential delivery must create a namespace-local Secret
with the `password` key before this release reconciles, and deliver the same
password separately to its consumer. The provisioning Job reads that Secret and
the existing operations administrator Secret directly; neither password belongs
in Helm values. The product also owns its consumer's exact ingress and egress
network-policy admission on operations PostgreSQL Pod port `9712` (Service port
`5432`). This chart admits only its provisioning Job from within the database
namespace. Do not select a product until its secret delivery, network rules,
ordering and consumer are reviewed together.

Names cannot collide with existing roles or databases. Existing objects require
this release's ownership marker, matching database owner, and no elevated role
privileges or memberships. For the one supported legacy pair, set
`addon.adoptExisting: true` with role `forgejo` and exactly one database named
`forgejo`, without `vector`. Before enabling adoption, the operator must verify
that the former provisioner no longer selects this database, the backup covers
the shared operations instance, and the product's password Secret matches its
current credential. The Job checks the existing login, database ownership, sole
database owned by the role, legacy database marker, role attributes, memberships,
and isolation before taking ownership. It refuses an incomplete or unexpected
pair; it never drops or recreates either object. This cannot independently prove
the old provisioner is disabled, so do not select the add-on until the operator
confirms that prerequisite. Reconciliation of an already adopted pair requires
matching markers for the same release. Adoption does not change the password or
database grants. Once the ownership markers are in place, set
`addon.adoptExisting: false` for normal password reconciliation; keep the same
role, database and release name.

The Job revokes PUBLIC database access and verifies that the add-on role cannot
reach other databases and that other non-superuser login roles cannot reach its
databases. Optional `vector` creates and checks the extension in the requested
databases. Changing the database list or removing the release never drops
databases, roles or passwords; coordinate any retirement or rotation with an
operator. All databases share the operations instance's backup and recovery
point.
