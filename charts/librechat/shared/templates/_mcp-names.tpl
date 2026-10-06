{{/* Route IDs only; native metadata and credentials never reach LibreChat. */}}
{{- define "frontend-librechat-shared.mcpNames" -}}
{{- $catalog := .Values.mcp.catalog | default dict -}}
{{- $presets := $catalog.presets | default dict -}}
{{- $custom := $catalog.custom | default list -}}
{{- $legacy := .Values.mcp.servers | default list -}}
{{- if and $legacy (or $presets $custom) -}}{{ fail "choose mcp.servers or mcp.catalog, not both" }}{{- end -}}
{{- $names := list -}}
{{- range $legacy -}}{{- $names = append $names .name -}}{{- end -}}
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
