# Integration Tests

Future live-cluster tests will cover deployment readiness, service-to-service behavior, and cross-application contracts whose primary assertion is not security-related.

These tests must use an explicitly selected controlled cluster and never default to a developer's current Kubernetes context.

The disposable `postgres-operations` clean-install and retry contract is covered
by `make live-postgres-acceptance`. It requires an explicit kubeconfig, context,
client identity, confirmation phrase, and storage class; it is never part of the
offline `make check` target.
