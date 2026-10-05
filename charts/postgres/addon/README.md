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

Names cannot collide with existing roles or databases. Existing objects are
accepted only with this release's ownership marker, matching database owner,
and no elevated role privileges or memberships. The Job revokes PUBLIC database
access and verifies that the new role cannot reach other databases and that
other non-superuser login roles cannot reach its databases. Optional `vector`
creates and checks the extension in the requested databases. Changing the
database list or removing the release never drops databases, roles or passwords;
coordinate any retirement or rotation with an operator. All databases share the
operations instance's backup and recovery point. This chart does not replace
the existing single-slot add-on provisioning in older Base releases.
