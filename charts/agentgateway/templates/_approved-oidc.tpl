{{/* Trusted chart configuration approves clients, never token permissions. */}}
{{- define "infra-agentgateway.approvedOidcAuthorization" -}}
{{- $root := .root -}}
{{- $bindings := .bindings -}}
{{- $pathsById := dict -}}
{{- range .bindings -}}
{{- $prefix := .prefix -}}
{{- $paths := list -}}
{{- range $operation := .operations -}}
{{- range $path := $operation.paths -}}
{{- $paths = append $paths (printf "%s%s" $prefix $path) -}}
{{- end -}}
{{- end -}}
{{- $_ := set $pathsById .id $paths -}}
{{- end -}}
{{- $clients := $root.Values.aiGateway.approvedOidcClients -}}
{{- if eq (kindOf $clients) "invalid" -}}
{{- $clients = list -}}
{{- end -}}
{{- if not (kindIs "slice" $clients) }}{{ fail "aiGateway.approvedOidcClients must be null or a list" }}{{ end -}}
{{- $seen := dict -}}
{{- $matches := list -}}
{{- $reserved := list "agentgateway" "studio" "keycloak-api-key-bridge" "account" "account-console" "admin-cli" "broker" "realm-management" "security-admin-console" ($root.Values.authKeycloak.studioClientId | default "studio") -}}
{{- range $client := $clients -}}
{{- if has $client.clientId $reserved }}{{ fail "approvedOidcClients cannot override reserved builtin clients" }}{{ end -}}
{{- if hasKey $seen $client.clientId }}{{ fail "approvedOidcClients clientId must be unique" }}{{ end -}}
{{- $_ := set $seen $client.clientId true -}}
{{- $paths := list -}}
{{- range $id := $client.bindings -}}
{{- if not (hasKey $pathsById $id) }}{{ fail (printf "approvedOidcClients references unavailable API binding %s" $id) }}{{ end -}}
{{- $paths = concat $paths (get $pathsById $id) -}}
{{- end -}}
{{- $discovery := $root.Values.aiGateway.localModelDiscovery | default dict -}}
{{- $extra := "false" -}}
{{- if and $discovery.enabled (has $discovery.bindingId $client.bindings) -}}
{{- $discoveryPaths := list "/local/api/v1/models" -}}
{{- range $binding := $bindings -}}
{{- if eq $binding.id $discovery.bindingId -}}
{{- range $model := $binding.models }}{{ $discoveryPaths = append $discoveryPaths (printf "/local/api/v1/models/%s" $model.name) }}{{ end -}}
{{- end -}}
{{- end -}}
{{- $extra = printf `request.method == "GET" && request.path in %s` ($discoveryPaths | toJson) -}}
{{- end -}}
{{- $matches = append $matches (printf `(jwt.azp == %s && (request.path in %s || (%s)))` ($client.clientId | toJson) ($paths | toJson) $extra) -}}
{{- end -}}
{{- $expression := printf `(has(jwt.aud) && ((type(jwt.aud) == string && jwt.aud == "agentgateway") || (type(jwt.aud) == list && "agentgateway" in jwt.aud)) && (%s))` (join " || " $matches | default "false") -}}
{{- if gt (len $expression) 8000 }}{{ fail "approvedOidcClients exceeds native authorization expression bounds" }}{{ end -}}
{{- $expression -}}
{{- end -}}
