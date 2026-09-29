# Notice preference rollout contract (staged)

The chart wiring is opt-in. The pinned Studio API `0.11.0`, extProc `0.13.0`,
and currently pinned API-key bridge must not be assumed to implement this
feature. Keep the database, private listener, extProc lookup and gateway
credential-context switches off until matching service images have been
released, verified, pinned and rolled out in dependency order:

1. Provision the same new database password in OpenBao at
   `frontend-studio/internal:postgresqlPassword` and
   `infra-postgres-operations/internal:studioPassword` using the supported
   credential-copy workflow. The two ExternalSecrets deliver separate
   namespace-local Secrets through the optional
   `releases/studio/secret-sync/` package, composed after those records exist;
   no password belongs in a ConfigMap or chart value.
2. Enable `studio.enabled` in the operations PostgreSQL chart. Its Job must
   finish provisioning and checking the `studio` role and database before
   enabling `frontendStudio.api.postgres.enabled` in the Studio API chart.
3. Roll out a compatible extProc image before a Studio image returning the new
   response shape. extProc must accept both the legacy five-field response and
   the nine-field response below during the transition, treating the four
   absent new fields as `true`. It must verify the private Studio Service DNS
   against the internal CA, present its dedicated client certificate, bound
   preference lookup time, and show all optional notices on lookup failure.
4. Studio API must implement the configured Postgres environment variables,
   persistent preferences and the dedicated HTTPS listener selected by
   `frontendStudio.api.noticePreferences.enabled`. Its private
   `GET /internal/v1/notice-preferences` must require a valid client certificate
   with CN `monitor-agentgateway-extproc-studio` and validate the trusted
   principal and optional personal-key identifier/kind. The effective response
   contains exactly nine booleans: `notices_enabled`, `show_no_pii`, `show_pass`,
   `show_changes`, `show_reroutes`, `show_timing`, `show_no_faces`,
   `show_detected_faces`, and `show_unscanned_faces`. Each defaults to `true`;
   Studio resolves each personal-key `inherit`/`on`/`off` override against its
   user's setting independently. `notices_enabled: false` suppresses all
   optional notices regardless of the other eight values. Face notices are
   separate from the five text/timing notices; these settings change display
   only, never blocks, errors, policy decisions or reports. No browser bearer
   token or caller-controlled identity may authorize this route. The public
   Studio HTTPRoute must never point to its private Service. After verifying
   the private API with the new extProc image running, enable
   `monitorAgentgatewayExtproc.noticePreferences.enabled`.
5. A compatible bridge release may return optional `credential_id` and
   `credential_kind` in its trusted v1 auth header. After the compatible
   extProc is running, enable
   `guardrails.llmPolicyEngine.credentialContextEnabled`. This adds
    `credential_context_version: "1"` and the credential fields to
    API-key model destination metadata only; JWT and MCP metadata omit them.
   With the switch off, neither responseMetadata nor destination metadata
   contains any new fields. The existing attachment `contract_version` stays
   independent of this credential-context version.

The three service implementations and the exact image pins still need to be
coordinated with their owning repositories. Chart rendering alone cannot
establish that their environment names and wire schemas match.
