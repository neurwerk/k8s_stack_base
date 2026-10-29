{{- define "infra-agentgateway.aiCatalog" -}}
{{- $bindings := include "infra-agentgateway.aiBindings" . | fromJsonArray }}
{{- $revision := include "infra-agentgateway.aiRevision" . }}
{{- $profileMap := .Files.Get "catalog/ai-profiles.yaml" | fromYaml }}
{{- $profiles := list }}
{{- range $id := keys $profileMap | sortAlpha }}
{{- $profile := deepCopy (get $profileMap $id) }}
{{- range $field := list "path" "proxyPath" "providers" "credentialHeader" }}{{ $_ := unset $profile $field }}{{ end }}
{{- $_ := set $profile "id" $id }}
{{- $profiles = append $profiles $profile }}
{{- end }}
{{- $profiles = append $profiles (dict "id" "mcp-streamable-http-v1" "revision" "1" "operations" (list (dict "id" "exchange" "support" "unknown" "restoration_scope" "request" "limitations" (list "Stateless MCP exchanges; each configured integration requires qualification."))) "limitations" (list "Stateful sessions are unsupported.")) }}
{{- $profiles = append $profiles (dict "id" "openai-model-discovery-v1" "revision" "1" "operations" (list (dict "id" "list" "support" "unknown" "restoration_scope" "none" "limitations" (list "GET /v1/models only.")) (dict "id" "retrieve" "support" "unknown" "restoration_scope" "none" "limitations" (list "GET /v1/models/{model} only."))) "limitations" (list "Configured, permission-filtered models only; discovery does not grant invocation.")) }}
{{- $catalog := dict "schema_version" 3 "policy_revision" $revision "profiles" $profiles "self-hosted-api-providers" list "external-api-providers" list "external-subscription-providers" list "mcp-servers" list }}
{{- $origin := printf "https://%s" .Values.infraAgentgatewayWrapper.hostname }}
{{- $endpoints := dict }}{{- $destinationKeys := dict }}{{- $entryIds := dict }}
{{- range $binding := $bindings }}
{{- $_ := set $entryIds $binding.id true }}
{{- $routes := list }}{{- $endpointIds := list }}{{- $destinations := list }}
{{- if $binding.api }}
{{- $_ := required "API catalog endpoints require infraAgentgatewayWrapper.hostname" $.Values.infraAgentgatewayWrapper.hostname }}
{{- $id := printf "api-%s" $binding.id }}
{{- $endpointIds = append $endpointIds $id }}
{{- $route := dict "id" $id "url" (printf "%s%s/v1" $origin $binding.prefix) "entry_mode" "api" "caller_auth_modes" (list "neurwerk_bearer" "oidc_bearer") "credential_mode_header" "X-Neurwerk-Credential-Mode" }}
{{- $routes = append $routes $route }}{{- $_ := set $endpoints $id $route }}
{{- end }}
{{- if $binding.inspect }}
{{- $id := "forward-proxy" }}{{- $endpointIds = append $endpointIds $id }}
{{- $route := dict "id" $id "url" (printf "https://%s:%d" $.Values.aiGateway.proxy.hostname (int $.Values.aiGateway.proxy.publicPort)) "entry_mode" "forward_proxy" "caller_auth_modes" (list "proxy_basic") }}
{{- $routes = append $routes $route }}{{- $_ := set $endpoints $id $route }}
{{- end }}
{{- $modes := list }}
{{- range $source := $binding.credentialSources }}
{{- $permissions := list }}
{{- if eq $source "global" }}{{ $permissions = list (printf "provider:%s:global-use" $binding.providerId) }}{{ end }}
{{- $modes = append $modes (dict "source" $source "required_permissions" $permissions) }}
{{- end }}
{{- range $model := $binding.models }}
{{- $profileIds := $binding.profiles }}
{{- if $binding.discovery }}{{ $profileIds = append $profileIds "openai-model-discovery-v1" }}{{ end }}
{{- $destination := dict "id" $model.name "kind" "model" "display_name" $model.displayName "endpoint_ids" $endpointIds "profile_ids" $profileIds "required_permissions" (get $binding.modelBindings $model.name).required_permissions "credential_modes" $modes "provider_id" $binding.providerId }}
{{- $destinations = append $destinations $destination }}{{- $_ := set $destinationKeys (printf "model:%s" $model.name) true }}
{{- end }}
{{- $owner := dict "id" $binding.id "provider_id" $binding.providerId "display_name" $binding.displayName "routes" $routes "destinations" $destinations }}
{{/* Published configuration status does not establish runtime credential readiness. */}}
{{- $globalStatus := "disabled" }}
{{- if has "global" ($binding.configuredCredentialSources | default $binding.credentialSources) }}
{{- $globalStatus = ternary "configured" "missing" (has "global" $binding.credentialSources) }}
{{- end }}
{{- $_ := set $owner "global_credential_status" $globalStatus }}
{{- $_ := set $catalog $binding.group (append (get $catalog $binding.group) $owner) }}
{{- end }}
{{- $mcp := index .Values "mcp-servers" }}
{{- if and $mcp.enabled .Values.externalGateway.enabled }}
{{- range $server := include "infra-agentgateway.effectiveMcpServers" . | fromYamlArray }}
{{- if or (not $server.contextforge) (has $server.name $mcp.contextforgeRoutesEnabled) }}
{{- $id := printf "mcp-%s" $server.name }}
{{- if hasKey $entryIds $id }}{{ fail "catalog entry IDs must be globally unique" }}{{ end }}
{{- $_ := set $entryIds $id true }}
{{- $route := dict "id" $id "url" (printf "%s/mcp/%s" $origin $server.name) "entry_mode" "api" "caller_auth_modes" (list "neurwerk_bearer" "oidc_bearer") "credential_mode_header" "X-Neurwerk-Credential-Mode" }}
{{- $destination := dict "id" $server.name "kind" "mcp" "display_name" ($server.displayName | default $server.name) "endpoint_ids" (list $id) "profile_ids" (list "mcp-streamable-http-v1") "required_permissions" (list "llm:invoke" (printf "mcp:%s:invoke" $server.name)) "credential_modes" (list (dict "source" "none" "required_permissions" (list))) "provider_id" nil }}
{{- $key := printf "mcp:%s" $server.name }}
{{- if hasKey $destinationKeys $key }}{{ fail "duplicate catalog destination" }}{{ end }}
{{- $_ := set $destinationKeys $key true }}{{- $_ := set $endpoints $id $route }}
{{- $_ := set $catalog "mcp-servers" (append (get $catalog "mcp-servers") (dict "id" $id "provider_id" nil "display_name" $destination.display_name "routes" (list $route) "destinations" (list $destination))) }}
{{- end }}
{{- end }}
{{- end }}
{{- if or (gt (len $endpoints) 256) (gt (len $destinationKeys) 500) (gt (len $profiles) 128) }}{{ fail "effective gateway catalog exceeds limits: 256 unique endpoints, 500 destinations, 128 profiles" }}{{ end }}
{{- $_ := set $catalog "catalog_revision" (sha256sum (toJson $catalog)) }}
{{- if gt (len (toJson $catalog)) 1048576 }}{{ fail "gateway catalog exceeds 1 MiB document limit" }}{{ end }}
{{- $catalog | toJson -}}
{{- end -}}
