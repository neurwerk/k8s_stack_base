{{- define "infra-agentgateway.modelResourceName" -}}
{{- regexReplaceAll "[^a-z0-9.-]" (. | lower) "-" -}}
{{- end -}}

{{- define "infra-agentgateway.mcpResourceName" -}}
{{- printf "mcp-%s-%s" .id .component -}}
{{- end -}}
