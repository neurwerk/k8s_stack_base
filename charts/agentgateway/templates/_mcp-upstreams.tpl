{{- define "infra-agentgateway.mcpUpstreamWorkloads" -}}
{{- $upstreams := deepCopy (.Values.mcp.upstreamWorkloads | default list) -}}
{{- if not (kindIs "slice" $upstreams) -}}{{ fail "mcp.upstreamWorkloads must be a list" }}{{- end -}}
{{- $overrides := dict -}}
{{- range $upstreams -}}{{- $_ := set $overrides .name true -}}{{- end -}}
{{- $presets := .Files.Get "catalog/presets.yaml" | fromYaml -}}
{{- range $id, $selection := .Values.mcp.catalog.presets | default dict -}}
{{- $preset := get $presets $id | default dict -}}
{{- if and $selection.enabled $preset.upstreamWorkload (not (hasKey $overrides $id)) -}}
{{- $upstreams = append $upstreams (deepCopy $preset.upstreamWorkload) -}}
{{- end -}}
{{- end -}}
{{- if gt (len $upstreams) 200 -}}{{ fail "mcp.upstreamWorkloads supports at most 200 entries" }}{{- end -}}
{{- $native := dict -}}
{{- range include "infra-agentgateway.effectiveMcpServers" . | fromYamlArray -}}
{{- if .contextforge -}}{{- $_ := set $native .name . -}}{{- end -}}
{{- end -}}
{{- $seen := dict -}}
{{- range $upstream := $upstreams -}}
{{- if not (kindIs "map" $upstream) -}}{{ fail "MCP upstream workload entries must be maps" }}{{- end -}}
{{- range $field, $_ := $upstream -}}
{{- if not (has $field (list "name" "port" "path" "workload")) -}}{{ fail "MCP upstreamWorkloads accepts only name, port, path and workload" }}{{- end -}}
{{- end -}}
{{- $id := required "MCP upstream workload requires name" $upstream.name -}}
{{- if or (not (kindIs "string" $id)) (gt (len $id) 48) (not (regexMatch `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$` $id)) -}}{{ fail "MCP upstream workload name must be a 1-48 character DNS label" }}{{- end -}}
{{- if or (hasKey $seen $id) (not (hasKey $native $id)) -}}{{ fail "MCP upstream workload names must be unique and match selected native catalog entries" }}{{- end -}}
{{- $_ := set $seen $id true -}}
{{- if not (kindIs "map" $upstream.workload) -}}{{ fail "MCP upstream workload requires a workload map" }}{{- end -}}
{{- if not $upstream.workload.image -}}{{ fail "MCP upstream workload.image is required" }}{{- end -}}
{{- range $field, $_ := $upstream.workload -}}
{{- if not (has $field (list "image" "imagePullPolicy" "replicas" "env" "secretEnv" "healthPath" "resources")) -}}{{ fail "unsupported MCP upstream workload setting" }}{{- end -}}
{{- end -}}
{{- range $ref := $upstream.workload.secretEnv | default list -}}
{{- if or (not (kindIs "map" $ref)) (ne (len $ref) 2) (empty $ref.name) (empty $ref.key) -}}{{ fail "MCP upstream secretEnv accepts only name/key references" }}{{- end -}}
{{- end -}}
{{- $port := $upstream.port | default 8080 -}}
{{- if or (not (regexMatch `^[1-9][0-9]*$` (toJson $port))) (gt (int $port) 65535) -}}{{ fail "MCP upstream port must be an integer from 1 to 65535" }}{{- end -}}
{{- $path := $upstream.path | default "/mcp" -}}
{{- $service := include "infra-agentgateway.mcpResourceName" (dict "id" $id "component" "svc") -}}
{{- $url := printf "http://%s.infra-agentgateway.svc.cluster.local:%d%s" $service (int $port) $path -}}
{{- $entry := get $native $id -}}
{{- if ne ($entry.registration.upstream_url | default "") $url -}}{{ fail "native registration.upstream_url must match its retained upstream workload Service, port and path" }}{{- end -}}
{{- if $.Values.mcp.studioSetup.enabled -}}
{{- if and $entry.credential (eq $entry.credential.method "upstream-env") -}}
{{- $_ := set $upstream "credential" $entry.credential -}}
{{- if ne (len ($upstream.workload.secretEnv | default list)) 1 -}}{{ fail "MCP upstream-env requires exactly one apiKey Secret environment reference" }}{{- end -}}
{{- if ne (first $upstream.workload.secretEnv).key "apiKey" -}}{{ fail "MCP upstream-env Secret reference must use key apiKey" }}{{- end -}}
{{- if eq $id "brave" -}}{{- $_ := unset $upstream.workload.env "BRAVE_MCP_ENABLED_TOOLS" -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $upstreams | toYaml -}}
{{- end -}}
