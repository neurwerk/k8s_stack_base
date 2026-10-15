# AgentGateway External Processor

The internal gRPC adapter inspects approved request content and restores protected
response text. AgentGateway owns provider routing and credentials; the adapter
requires verified version-5 admission. See the [gateway contract](../agentgateway/README.md).

Raw rejected-request capture is removed. The chart supplies no capture volume,
exporter credentials, debug ServiceAccount or Langfuse network access. Rejections
use content-free, request-correlated diagnostics; detailed reasons require a
compatible processor image.

## Historical debug data

Keep `rejectedRequestDebug.enabled: true` in Langfuse until historical records have
expired and deletion has been verified. Its isolated project credentials and hourly
cleanup remain owned by the Langfuse chart. The separately pinned cleanup image runs
`python -m agentgateway_extproc.lib.rejection_retention` and expires records older
than 72 hours through the project API. Deletion is asynchronous; an accepted request
is not proof that records are gone. The gateway no longer needs Langfuse startup
ordering or debug credentials. This chart does not revoke the OpenBao source key.
