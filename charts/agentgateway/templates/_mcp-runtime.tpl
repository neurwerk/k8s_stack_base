{{- define "infra-agentgateway.mcpCredentialVersion" -}}
{{- $record := get .root.Values.mcp.runtimeCredentials .id | default (dict "version" "initial" "kvVersion" 1 "configured" false) -}}
{{- if or (not (kindIs "map" $record)) (ne (len $record) 3) (not (kindIs "bool" $record.configured)) (not (regexMatch `^(initial|[a-f0-9]{32})$` (toString $record.version))) (not (regexMatch `^[1-9][0-9]*$` (toString $record.kvVersion))) -}}
{{- fail "Invalid ESO MCP credential version metadata" -}}
{{- end -}}
{{- $record | toYaml -}}
{{- end -}}

{{- define "infra-agentgateway.mcpCredentialName" -}}
{{- printf "mcp-key-%s-%s" (.id | sha256sum | trunc 12) .version -}}
{{- end -}}

{{- define "infra-agentgateway.mcpForwardName" -}}
{{- printf "mcp-forward-%s-%s" (.id | sha256sum | trunc 12) .version -}}
{{- end -}}
