{{/* Same canonical selection as AgentGateway; this chart needs route IDs only. */}}
{{- define "librechat-oidc.mcpNames" -}}
{{- $catalog := .Values.mcp.catalog | default dict -}}
{{- $presets := $catalog.presets | default dict -}}
{{- $custom := $catalog.custom | default list -}}
{{- if .Values.mcp.servers }}{{ fail "mcp.servers has been removed; configure mcp.catalog" }}{{ end -}}
{{- $names := list -}}
{{- range $id, $selection := $presets -}}
{{- if not (kindIs "bool" $selection.enabled) -}}{{ fail "MCP preset selection requires boolean enabled" }}{{- end -}}
{{- if $selection.enabled -}}{{- $names = append $names $id -}}{{- end -}}
{{- end -}}
{{- range $custom -}}
{{- if not (kindIs "bool" .enabled) -}}{{ fail "MCP custom selection requires boolean enabled" }}{{- end -}}
{{- if .enabled -}}{{- $names = append $names .name -}}{{- end -}}
{{- end -}}
{{- $names | toYaml -}}
{{- end -}}
