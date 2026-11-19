{{- define "infra-agentgateway.mcpUsage" -}}
{{- $dimensions := dict "server" "mcp.tool.target" "tool" "mcp.tool.name" -}}
{{- range $field := list "principal_id" "app_id" "credential_id" "credential_kind" "entry_mode" "policy_revision" -}}
{{- $_ := set $dimensions $field (printf "has(metadata.neurwerk_identity.%s) ? metadata.neurwerk_identity.%s : null" $field $field) -}}
{{- end -}}
{{- range $field := list "destination_kind" "destination_id" "provider_id" "binding_id" "protocol_profile" "operation_id" "provider_credential_source" "billing_classification" -}}
{{- $_ := set $dimensions $field (printf "has(metadata.neurwerk_admission.%s) ? metadata.neurwerk_admission.%s : null" $field $field) -}}
{{- end -}}
{{- $pairs := list -}}
{{- range $name, $expression := $dimensions -}}
{{- $pairs = append $pairs (printf "%s: (%s)" ($name | toJson) $expression) -}}
{{- end -}}
has(proxy.listener.name) && proxy.listener.name == "api-bindings"
  && has(mcp.methodName) && mcp.methodName == "tools/call"
  && has(mcp.tool.target) && has(mcp.tool.name)
  ? {"version": 1, "id": uuid(), "occurred_at": string(request.endTime),
     "dimensions": { {{ join ", " $pairs }} }}
  : null
{{- end -}}
