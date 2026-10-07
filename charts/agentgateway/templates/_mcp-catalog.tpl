{{/* One effective server list. Legacy entries retain their historical defaults. */}}
{{- define "infra-agentgateway.effectiveMcpServers" -}}
{{- $catalog := .Values.mcp.catalog | default dict -}}
{{- range $field, $_ := $catalog -}}
{{- if not (has $field (list "presets" "custom")) -}}{{- fail "mcp.catalog accepts only presets and custom" -}}{{- end -}}
{{- end -}}
{{- $selected := $catalog.presets | default dict -}}
{{- $custom := $catalog.custom | default list -}}
{{- if or (not (kindIs "map" $selected)) (not (kindIs "slice" $custom)) -}}
{{- fail "mcp.catalog.presets must be a map and custom must be a list" -}}
{{- end -}}
{{- $legacy := .Values.mcp.servers | default list -}}
{{- if and (gt (len $legacy) 0) (or (gt (len $selected) 0) (gt (len $custom) 0)) -}}
{{- fail "choose mcp.servers or mcp.catalog, not both; migration is explicit" -}}
{{- end -}}
{{- $presets := .Files.Get "catalog/presets.yaml" | fromYaml -}}
{{- $entries := list -}}
{{- range $id, $selection := $selected -}}
{{- if not (hasKey $presets $id) -}}{{- fail (printf "unknown MCP preset %q; use mcp.catalog.custom for client definitions" $id) -}}{{- end -}}
{{- if not (kindIs "map" $selection) -}}{{- fail "MCP preset selection must be a map" -}}{{- end -}}
{{- range $field, $_ := $selection -}}
{{- if not (has $field (list "enabled" "displayName" "piiEnabled" "contentTracingEnabled" "contextforge" "registration")) -}}
{{- fail (printf "MCP preset selection field %q is unsupported" $field) -}}
{{- end -}}
{{- end -}}
{{- $entry := mergeOverwrite (deepCopy (get $presets $id)) (deepCopy $selection) -}}
{{- $_ := unset $entry "upstreamWorkload" -}}
{{- if or (ne $entry.contextforge.provider (get $presets $id).contextforge.provider) (ne $entry.contextforge.authenticationModel (get $presets $id).contextforge.authenticationModel) -}}
{{- fail "preset provider/authentication model cannot change; use a custom definition" -}}
{{- end -}}
{{- $entries = append $entries $entry -}}
{{- end -}}
{{- $entries = concat $entries $custom -}}
{{- $effective := list -}}
{{- range $entry := $entries -}}
{{- if not (kindIs "map" $entry) -}}{{- fail "MCP catalog entries must be maps" -}}{{- end -}}
{{- if or (not (hasKey $entry "enabled")) (not (kindIs "bool" $entry.enabled)) -}}
{{- fail "MCP catalog entries require explicit boolean enabled" -}}
{{- end -}}
{{- range $field, $_ := $entry -}}
{{- if not (has $field (list "enabled" "name" "displayName" "piiEnabled" "contentTracingEnabled" "contextforge" "registration" "host" "workload" "path" "port" "protocol" "tls" "upstreamAuth")) -}}
{{- fail (printf "MCP catalog field %q is unsupported; credentials must use Secret references" $field) -}}
{{- end -}}
{{- end -}}
{{- if $entry.enabled -}}
{{- $server := mergeOverwrite (dict "piiEnabled" true "contentTracingEnabled" false) (deepCopy $entry) -}}
{{- $_ := unset $server "enabled" -}}
{{- if not $server.displayName -}}{{- $_ := set $server "displayName" $server.name -}}{{- end -}}
{{- if or (not (kindIs "string" $server.displayName)) (gt (len $server.displayName) 128) -}}{{- fail "MCP catalog displayName must be a string of at most 128 characters" -}}{{- end -}}
{{- if hasKey $server "upstreamAuth" -}}
{{- if not (kindIs "map" $server.upstreamAuth) -}}{{- fail "MCP upstreamAuth must contain only header and optional Secret key" -}}{{- end -}}
{{- range $field, $_ := $server.upstreamAuth -}}
{{- if not (has $field (list "header" "key")) -}}{{- fail "MCP upstreamAuth accepts only header/key references, never plaintext credentials" -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- if $server.workload -}}
{{- range $ref := $server.workload.secretEnv | default list -}}
{{- if or (not (kindIs "map" $ref)) (ne (len $ref) 2) (empty $ref.name) (empty $ref.key) -}}{{- fail "MCP workload secretEnv accepts only name/key references" -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- if $server.contextforge -}}
{{- if not (kindIs "map" $server.registration) -}}{{- fail "native MCP catalog entries require non-secret registration metadata" -}}{{- end -}}
{{- $_ := include "infra-agentgateway.validateMcpRegistration" $server -}}
{{- else if hasKey $server "registration" -}}{{- fail "registration metadata requires contextforge" -}}{{- end -}}
{{- $effective = append $effective $server -}}
{{- end -}}
{{- end -}}
{{- if gt (len $effective) 200 -}}{{- fail "MCP catalog supports at most 200 selected entries" -}}{{- end -}}
{{- concat $legacy $effective | toYaml -}}
{{- end -}}

{{- define "infra-agentgateway.validateMcpRegistration" -}}
{{- $registration := .registration -}}
{{- range $field, $_ := $registration -}}
{{- if not (has $field (list "upstream_url" "transport" "approved_tools" "pii_policy" "visibility" "oauth")) -}}
{{- fail (printf "MCP registration field %q is unsupported; no plaintext credentials or headers" $field) -}}
{{- end -}}
{{- end -}}
{{- $url := required "MCP registration requires upstream_url" $registration.upstream_url -}}
{{- if or (not (kindIs "string" $url)) (not (regexMatch `^https?://[a-zA-Z0-9.-]+(:[0-9]+)?(/[a-zA-Z0-9_./-]*)?$` (toString $url))) (regexMatch `(^|/)\.\.?(/|$)` (toString $url)) -}}
{{- fail "MCP registration upstream_url must be an approved HTTP(S) endpoint without credentials, query or fragment" -}}
{{- end -}}
{{- if not (has $registration.transport (list "SSE" "STREAMABLEHTTP")) -}}{{- fail "MCP registration transport must be SSE or STREAMABLEHTTP" -}}{{- end -}}
{{- if or (not (kindIs "slice" $registration.approved_tools)) (empty $registration.approved_tools) (gt (len $registration.approved_tools) 100) -}}
{{- fail "MCP registration requires 1-100 exact approved_tools" -}}
{{- end -}}
{{- if ne (len $registration.approved_tools) (len (uniq $registration.approved_tools)) -}}{{- fail "MCP approved_tools must be unique" -}}{{- end -}}
{{- range $tool := $registration.approved_tools -}}
{{- if or (not (kindIs "string" $tool)) (empty $tool) -}}{{- fail "MCP approved_tools must be nonempty strings" -}}{{- end -}}
{{- end -}}
{{- if or (not (kindIs "string" $registration.pii_policy)) (empty $registration.pii_policy) -}}{{- fail "MCP registration requires a nonempty pii_policy identifier" -}}{{- end -}}
{{- if and (hasKey $registration "visibility") (not (has $registration.visibility (list "team" "public"))) -}}{{- fail "MCP registration visibility must be team or public behind private ingress" -}}{{- end -}}
{{- if hasKey $registration "oauth" -}}
{{- if ne .contextforge.authenticationModel "individual-authentication" -}}{{- fail "MCP oauth metadata requires individual-authentication" -}}{{- end -}}
{{- $oauth := $registration.oauth -}}
{{- if not (kindIs "map" $oauth) -}}{{- fail "MCP oauth must be a non-secret map" -}}{{- end -}}
{{- range $field, $_ := $oauth -}}
{{- if not (has $field (list "authorization_url" "token_url" "client_id" "redirect_uri" "scopes" "client_secret_ref" "pkce")) -}}{{- fail "unsupported MCP oauth field; plaintext secrets are forbidden" -}}{{- end -}}
{{- end -}}
{{- range $field := list "authorization_url" "token_url" "redirect_uri" -}}
{{- $url := get $oauth $field -}}
{{- if or (not (kindIs "string" $url)) (not (regexMatch `^https://[a-zA-Z0-9.-]+(:[0-9]+)?(/[a-zA-Z0-9_./-]*)?$` (toString $url))) (regexMatch `(^|/)\.\.?(/|$)` (toString $url)) -}}{{- fail "MCP oauth endpoints must be approved HTTPS URLs without credentials, query or fragment" -}}{{- end -}}
{{- end -}}
{{- if or (not (kindIs "string" $oauth.client_id)) (empty $oauth.client_id) -}}{{- fail "MCP oauth requires a nonempty client_id" -}}{{- end -}}
{{- if ne $oauth.pkce true -}}{{- fail "MCP oauth requires pkce:true" -}}{{- end -}}
{{- if not (kindIs "slice" $oauth.scopes) -}}{{- fail "MCP oauth scopes must be a list" -}}{{- end -}}
{{- range $scope := $oauth.scopes -}}{{- if or (not (kindIs "string" $scope)) (empty $scope) -}}{{- fail "MCP oauth scopes must be nonempty strings" -}}{{- end -}}{{- end -}}
{{- $ref := $oauth.client_secret_ref -}}
{{- if not (kindIs "map" $ref) -}}{{- fail "MCP oauth requires client_secret_ref with name and key" -}}{{- end -}}
{{- if or (ne (len $ref) 2) (empty $ref.name) (empty $ref.key) -}}{{- fail "MCP client_secret_ref requires only name and key" -}}{{- end -}}
{{- if or (not (kindIs "string" $ref.name)) (not (kindIs "string" $ref.key)) (not (regexMatch `^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$` (toString $ref.name))) (not (regexMatch `^[-._a-zA-Z0-9]+$` (toString $ref.key))) -}}{{- fail "MCP client_secret_ref name and key must be valid Secret identifiers" -}}{{- end -}}
{{- if or (ne $ref.name "contextforge-oauth-apps") (ne $ref.key .name) -}}{{- fail "MCP client_secret_ref must use contextforge-oauth-apps and the selected integration ID" -}}{{- end -}}
{{- end -}}
{{- end -}}
