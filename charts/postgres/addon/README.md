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

Names cannot collide with existing roles or databases. The Job keeps an
administrator-owned ledger in the shared `postgres` database. It commits the
release/role/database binding and pending database intent before `CREATE
DATABASE`, while holding a session advisory lock across creation and marking.
A retry accepts an unmarked database only with its pending intent, the exact
recorded role as owner, and no conflicting comment. A new release never adopts
preexisting roles or databases; marked existing objects require their recorded
binding. Missing completed objects, changed names, conflicting comments or
owners, elevated role privileges or memberships fail closed. An unrelated
privileged actor creating a matching database in the brief gap after intent
commits cannot be distinguished from the Job's own creation; restrict
administrator access accordingly. Vector setup happens after the creation lock
is released and is retried independently. The Job revokes PUBLIC database access
and verifies that the new role cannot reach other databases and that other
non-superuser login roles cannot reach its databases. Optional `vector` creates
and checks the extension in the requested databases. Changing the
database list or removing the release never drops databases, roles or passwords;
coordinate any retirement or rotation with an operator. All databases share the
operations instance's backup and recovery point.
