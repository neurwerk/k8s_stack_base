# Security Tests

Security tests protect cross-chart invariants that must not regress as applications evolve. The static MCP suite renders AgentGateway and LibreChat using synthetic values and verifies identity handling, JWT audience and `azp` restrictions, anonymous-route scope, LLM extProc separation, MCP routing, service exposure, Secret references, LibreChat token forwarding, and Langfuse telemetry expressions.

Run the offline static suite with:

```sh
make security-check
```

The static suite does not prove that a live JWT or API-key request succeeds. It does not require a Kubernetes cluster, Keycloak credentials, API keys, a live AgentGateway, Brave, or Langfuse.

Live tests are intentionally deferred until MCP is deployed to a controlled cluster. The future command is:

```sh
make security-integration
```

That future suite must receive real tokens and API keys only through environment variables or CI secrets, never through committed fixtures.

## Threat Model

The static suite covers identity spoofing, `azp` confusion, anonymous route expansion, LLM extProc bypass, arbitrary upstream proxying, secret leakage, and direct MCP Service exposure.
