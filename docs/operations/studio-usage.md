# Studio usage cutover

Adopt the Studio API, Web and collector images together using the exact pins in the selected manifest. Before adoption, reconcile the manifest's standalone `stack-setup` release to prepare separate collector and reporting-reader passwords, then provision `studio_usage` through the operations PostgreSQL chart. The API's initialization container creates the reporting schema before the API starts.

Start reporting fresh. Do not migrate, backfill or archive previous usage. Remove only verified obsolete Studio-owned analytics objects; active application tables and upstream AgentGateway, Prometheus and logging storage retain their owners. The collector records activation and retains its new hourly and daily summaries.

Supply client pricing through `products/studio/mcp-pricing.yaml` and the `studio-mcp-pricing-values` ConfigMap. Prices are USD per 1,000 tool calls; append dated revisions on reporting-hour boundaries. An explicit null tool price blocks the server default, and zero means free. Provider fees, allowances and credits apply once per billing account; tool costs and adjusted monthly estimates are alternative views.

Agree whether the client needs a brief maintenance window. After activation, verify Flux readiness, database roles, the collector job, source freshness, trusted MCP attribution, repeated imports, the LLM/MCP switch and existing drilldowns. MCP counts and prices remain monitoring estimates, and incomplete coverage remains visible.
