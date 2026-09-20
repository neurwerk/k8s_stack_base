# Security Tests

`mise exec -- make security-check` checks the live acceptance runner's opt-in,
context, credential and cleanup safeguards offline. Rendered security contracts
are in `tests/charts/`; these checks do not prove live authentication or networking.
