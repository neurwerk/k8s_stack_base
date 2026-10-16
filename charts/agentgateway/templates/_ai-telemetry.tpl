{{- define "infra-agentgateway.aiTelemetry" -}}
{{- $fields := dict -}}
{{- range $field := list "request_id" "entry_mode" "destination_kind" "provider_id" "binding_id" "protocol_profile" "operation_id" "provider_credential_source" "policy_revision" "credential_id" "credential_kind" -}}
{{- $_ := set $fields (printf "neurwerk.%s" $field) (printf "has(metadata.neurwerk_admission) ? metadata.neurwerk_admission.%s : null" $field) -}}
{{- end -}}
{{- range $field := list "destination_id" "requested_model" "resolved_model" "dispatch_binding_id" "dispatch_destination_id" -}}
{{- $_ := set $fields (printf "neurwerk.%s" $field) (printf `has(metadata.neurwerk_admission) ? default(extproc["neurwerk.request_context"].%s, null) : null` $field) -}}
{{- end -}}
{{/* default() cannot catch a field access on absent extProc metadata in native 1.6. */}}
{{- range $field := list "request_text_inspection" "target_credential_mode" "billing_classification" -}}
{{- $_ := set $fields (printf "neurwerk.%s" $field) (printf `has(metadata.neurwerk_admission) && extproc != null && "neurwerk.request_context" in extproc && has(extproc["neurwerk.request_context"].%s) ? extproc["neurwerk.request_context"].%s : "unknown"` $field $field) -}}
{{- end -}}
{{/* A selected target is not proof of an outbound attempt. Missing timing is unknown,
including immediate extProc rejections. HTTP 200 does not prove final stream success. */}}
{{- $_ := set $fields "neurwerk.dispatch_attempted" `has(metadata.neurwerk_admission) && has(proxy.upstreamDuration) ? true : null` -}}
{{- $_ := set $fields "neurwerk.request_outcome" `!has(metadata.neurwerk_admission) ? "unknown" : has(proxy.error) ? "gateway_error" : default(response.code >= 400 && response.code <= 599, false) ? "http_error" : "unknown"` -}}
{{/* Native 1.6.0 loses evidence needed for complete v5 accounting:
- Chat/Gemini parsers fill absent counters with zero; cost also coalesces usage.
- CEL exposes neither SSE completion nor Responses in-band status/error.
- Even buffered Messages retains HTTP 200, counters and cost when a later
  full-duplex extProc response-body rejection/abort terminates the response.
  That failure is not reliably exposed in proxy.error or late dynamic metadata.
No current profile proves successful final validation here. Preserve native
reported sums, including zero, independently; never promote them to known totals.
Historical/legacy requests keep absent flags. Revisit only with qualified native
raw-usage, completion and applicable-pricing evidence; no response-body capture. */}}
{{- range $field := list "token_usage_known" "cost_known" -}}
{{- $_ := set $fields (printf "neurwerk.%s" $field) `has(metadata.neurwerk_admission) ? false : null` -}}
{{- end -}}
{{- $fields | toYaml -}}
{{- end -}}
