{{- define "infra-agentgateway.aiTelemetry" -}}
{{- $fields := dict -}}
{{- range $field := list "request_id" "entry_mode" "destination_kind" "provider_id" "binding_id" "protocol_profile" "operation_id" "provider_credential_source" "policy_revision" "credential_id" "credential_kind" -}}
{{- $_ := set $fields (printf "neurwerk.%s" $field) (printf "has(metadata.neurwerk_admission) && has(metadata.neurwerk_admission.%s) ? metadata.neurwerk_admission.%s : null" $field $field) -}}
{{- end -}}
{{- range $field := list "request_id" "entry_mode" "policy_revision" "credential_id" "credential_kind" "app_id" -}}
{{- $_ := set $fields (printf "neurwerk.%s" $field) (printf "has(metadata.neurwerk_identity) && has(metadata.neurwerk_identity.%s) ? metadata.neurwerk_identity.%s : has(metadata.neurwerk_admission) && has(metadata.neurwerk_admission.%s) ? metadata.neurwerk_admission.%s : null" $field $field $field $field) -}}
{{- end -}}
{{- $_ := set $fields "neurwerk.matched_route" `has(proxy.route.name) ? proxy.route.name : null` -}}
{{- range $field := list "destination_id" "requested_model" "resolved_model" "dispatch_binding_id" "dispatch_destination_id" -}}
{{- $_ := set $fields (printf "neurwerk.%s" $field) (printf `has(metadata.neurwerk_admission) && extproc != null && "neurwerk.request_context" in extproc && has(extproc["neurwerk.request_context"].%s) ? extproc["neurwerk.request_context"].%s : null` $field $field) -}}
{{- end -}}
{{/* default() cannot catch a field access on absent extProc metadata in native 1.6. */}}
{{- range $field := list "request_text_inspection" "target_credential_mode" "billing_classification" -}}
{{- $_ := set $fields (printf "neurwerk.%s" $field) (printf `has(metadata.neurwerk_admission) && extproc != null && "neurwerk.request_context" in extproc && has(extproc["neurwerk.request_context"].%s) ? extproc["neurwerk.request_context"].%s : "unknown"` $field $field) -}}
{{- end -}}
{{- $includedBindings := list -}}
{{- if .Values.aiGateway.enabled -}}
{{- range $binding := include "infra-agentgateway.aiBindings" . | fromJsonArray -}}
{{- if and (eq ($binding.billingMode | default "") "included") (has "openai-codex-responses-v1" $binding.profiles) -}}
{{- $includedBindings = append $includedBindings $binding.id -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if $includedBindings -}}
{{- $existing := get $fields "neurwerk.billing_classification" -}}
{{- $_ := set $fields "neurwerk.billing_classification" (printf `has(metadata.neurwerk_admission) && metadata.neurwerk_admission.provider_credential_source == "override" && extproc != null && "neurwerk.request_context" in extproc && has(extproc["neurwerk.request_context"].dispatch_binding_id) && extproc["neurwerk.request_context"].dispatch_binding_id in %s ? "subscription" : (%s)` ($includedBindings | toJson) $existing) -}}
{{- end -}}
{{/* A selected target is not proof of an outbound attempt. Missing timing is unknown,
including immediate extProc rejections. HTTP 200 does not prove final stream success. */}}
{{- $_ := set $fields "neurwerk.dispatch_attempted" `has(metadata.neurwerk_admission) && has(proxy.upstreamDuration) ? true : null` -}}
{{- $_ := set $fields "neurwerk.request_outcome" `!has(metadata.neurwerk_admission) ? "unknown" : has(proxy.error) ? "gateway_error" : default(response.code >= 400 && response.code <= 599, false) ? "http_error" : "unknown"` -}}
{{/* Known native reported accounting, independent of final delivery success.
Native 1.6.0 may default missing Chat/Gemini counters to zero: these flags do
not prove raw counter completeness or stream completion. A catalog cost alone
is insufficient because native pricing also coalesces absent usage to zero.
Historical/legacy requests keep absent flags. */}}
{{- $usageKnown := `has(llm.inputTokens) && has(llm.outputTokens) && has(llm.totalTokens)` -}}
{{- $_ := set $fields "neurwerk.token_usage_known" (printf `has(metadata.neurwerk_admission) ? (%s) : null` $usageKnown) -}}
{{- $_ := set $fields "neurwerk.cost_known" (printf `has(metadata.neurwerk_admission) ? (%s && has(llm.cost) && has(llm.costRates)) : null` $usageKnown) -}}
{{- $fields | toYaml -}}
{{- end -}}
