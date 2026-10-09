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
{{- if not (has $field (list "enabled" "displayName" "piiEnabled" "contentTracingEnabled" "contextforge" "registration" "checks" "credential")) -}}
{{- fail (printf "MCP preset selection field %q is unsupported" $field) -}}
{{- end -}}
{{- end -}}
{{- $entry := mergeOverwrite (deepCopy (get $presets $id)) (deepCopy $selection) -}}
{{- $_ := unset $entry "upstreamWorkload" -}}
{{- if or (ne $entry.contextforge.provider (get $presets $id).contextforge.provider) (ne $entry.contextforge.authenticationModel (get $presets $id).contextforge.authenticationModel) (ne ($entry.credential | toJson) ((get $presets $id).credential | toJson)) -}}
{{- fail "preset provider/authentication or credential policy cannot change; use a custom definition" -}}
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
{{- if not (has $field (list "enabled" "name" "displayName" "piiEnabled" "contentTracingEnabled" "contextforge" "registration" "checks" "credential" "host" "workload" "path" "port" "protocol" "tls" "upstreamAuth")) -}}
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
{{- $_ := include "infra-agentgateway.validateMcpChecks" $server -}}
{{- $credential := $server.credential | default dict -}}
{{- if $credential -}}
{{- if or (not (kindIs "map" $credential)) (not (hasKey $credential "owner")) (not (hasKey $credential "required")) (not (hasKey $credential "method")) -}}{{- fail "MCP credential policy requires owner, required and method" -}}{{- end -}}
{{- range $key, $_ := $credential -}}{{- if not (has $key (list "owner" "required" "method" "header")) -}}{{- fail "MCP credential policy contains an unsupported field" -}}{{- end -}}{{- end -}}
{{- if eq $credential.method "gateway-header" -}}
{{- if not (regexMatch `^[a-zA-Z0-9_-]{1,100}$` ($credential.header | default "")) -}}{{- fail "MCP gateway-header requires an approved header name" -}}{{- end -}}
{{- else if $credential.header -}}{{- fail "MCP header is only supported for gateway-header credentials" -}}{{- end -}}
{{- if or (not (has $credential.owner (list "none" "shared" "individual"))) (not (kindIs "bool" $credential.required)) (not (has $credential.method (list "none" "upstream-env" "gateway-header" "oauth"))) -}}{{- fail "Invalid MCP credential policy" -}}{{- end -}}
{{- if or (and (eq $credential.owner "none") (or $credential.required (ne $credential.method "none"))) (and (eq $credential.owner "shared") (not (has $credential.method (list "upstream-env" "gateway-header")))) (and (eq $credential.owner "individual") (or (not $credential.required) (ne $credential.method "oauth"))) -}}{{- fail "MCP credential owner and method conflict" -}}{{- end -}}
{{- if or (and (eq $server.contextforge.authenticationModel "individual-authentication") (ne $credential.owner "individual")) (and (ne $server.contextforge.authenticationModel "individual-authentication") (eq $credential.owner "individual")) (and (eq $server.contextforge.authenticationModel "shared-authentication") (ne $credential.owner "shared")) -}}{{- fail "MCP credential policy conflicts with authentication model" -}}{{- end -}}
{{- end -}}
{{- else if hasKey $server "registration" -}}{{- fail "registration metadata requires contextforge" -}}{{- end -}}
{{- $effective = append $effective $server -}}
{{- end -}}
{{- end -}}
{{- if gt (len $effective) 200 -}}{{- fail "MCP catalog supports at most 200 selected entries" -}}{{- end -}}
{{- concat $legacy $effective | toYaml -}}
{{- end -}}

{{/* Checks are explicit operator approval of read-only calls with non-secret arguments. */}}
{{- define "infra-agentgateway.validateMcpChecks" -}}
{{- $checks := .checks | default list -}}
{{- if or (not (kindIs "slice" $checks)) (gt (len $checks) 10) -}}{{- fail "MCP checks must be a list of at most 10 approved read-only calls" -}}{{- end -}}
{{- $approved := .registration.approved_tools -}}
{{- range $check := $checks -}}
{{- if not (kindIs "map" $check) -}}{{- fail "MCP check must be a map" -}}{{- end -}}
{{- range $field, $_ := $check -}}
{{- if not (has $field (list "name" "tool" "arguments" "display")) -}}{{- fail "MCP checks accept only name, tool, arguments and optional display" -}}{{- end -}}
{{- end -}}
{{- if or (not (kindIs "string" $check.name)) (empty $check.name) (gt (len $check.name) 100) -}}{{- fail "MCP check name must contain 1-100 characters" -}}{{- end -}}
{{- if not (has $check.tool $approved) -}}{{- fail "MCP check tool must be in registration.approved_tools" -}}{{- end -}}
{{- if or (not (kindIs "map" $check.arguments)) (gt (len (toJson $check.arguments)) 8192) -}}{{- fail "MCP check arguments must be a non-secret JSON object of at most 8192 bytes" -}}{{- end -}}
{{- if $check.display -}}
{{- if not (kindIs "map" $check.display) -}}{{- fail "MCP check display must contain label and field" -}}{{- end -}}
{{- if or (ne (len $check.display) 2) (not (kindIs "string" $check.display.label)) (empty $check.display.label) (gt (len $check.display.label) 100) (not (kindIs "string" $check.display.field)) (gt (len $check.display.field) 200) (not (regexMatch `^[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)*$` $check.display.field)) -}}
{{- fail "MCP check display requires a label and dot-separated JSON field" -}}
{{- end -}}
{{- end -}}
{{- end -}}
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
