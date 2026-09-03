{{- define "infra-agentgateway.modelResourceName" -}}
{{- $normalized := regexReplaceAll "[^a-z0-9.-]" (. | lower) "-" -}}
{{- if le (len $normalized) 50 -}}
{{- $normalized -}}
{{- else -}}
{{- printf "%s-%s" (trunc 41 $normalized | trimSuffix "-" | trimSuffix ".") (sha256sum $normalized | trunc 8) -}}
{{- end -}}
{{- end -}}

{{- define "infra-agentgateway.mcpResourceName" -}}
{{- printf "mcp-%s-%s" .id .component -}}
{{- end -}}

{{- define "infra-agentgateway.effectiveModels" -}}
{{- $catalog := .Values.openrouterCatalog.models | default list -}}
{{- $exclusions := .Values.openrouterCatalog.excludedModels | default list -}}
{{- if ne (len $exclusions) (len ($exclusions | uniq)) -}}
{{- fail "openrouterCatalog.excludedModels must not contain duplicates" -}}
{{- end -}}
{{- $catalogUpstreams := dict -}}
{{- $catalogNames := dict -}}
{{- range $entry := $catalog -}}
{{- $name := required "openrouterCatalog.models[].name is required" $entry.name -}}
{{- $upstream := required "openrouterCatalog.models[].upstreamModel is required" $entry.upstreamModel -}}
{{- if hasKey $catalogUpstreams $upstream }}{{- fail (printf "OpenRouter upstream model %q is duplicated" $upstream) }}{{- end -}}
{{- if hasKey $catalogNames $name }}{{- fail (printf "OpenRouter public model name %q is duplicated" $name) }}{{- end -}}
{{- $_ := set $catalogUpstreams $upstream true -}}
{{- $_ := set $catalogNames $name true -}}
{{- end -}}
{{- range $upstream := $exclusions -}}
{{- if not (hasKey $catalogUpstreams $upstream) }}{{- fail (printf "openrouterCatalog.excludedModels contains unknown upstream model %q" $upstream) }}{{- end -}}
{{- end -}}
{{- $clientModels := .Values.guardrails.llmPolicyEngine.models | default list -}}
{{- $clientNames := dict -}}
{{- range $model := $clientModels -}}
{{- $name := required "guardrails.llmPolicyEngine.models[].name is required" $model.name -}}
{{- if hasKey $clientNames $name }}{{- fail (printf "model name %q is duplicated" $name) }}{{- end -}}
{{- $_ := set $clientNames $name true -}}
{{- end -}}
{{- $effective := list -}}
{{- if .Values.openrouterCatalog.enabled -}}
{{- range $entry := $catalog -}}
{{- if and (not (has $entry.upstreamModel $exclusions)) (not (hasKey $clientNames $entry.name)) -}}
{{- $effective = append $effective (dict "name" $entry.name "provider" "Openrouter" "model" $entry.upstreamModel "baseURL" "https://openrouter.ai/api/v1" "authSecret" "infra-agentgateway-secret" "piiEnabled" true "contentTracingEnabled" true "piiReroute" true) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- range $model := $clientModels }}{{- $effective = append $effective $model }}{{- end -}}
{{- $effective | toYaml -}}
{{- end -}}

{{- define "infra-agentgateway.effectiveRoles" -}}
{{- $roles := .Values.authKeycloak.agentgatewayClientRoles | default list | uniq -}}
{{- if .Values.openrouterCatalog.enabled -}}
{{- $excluded := .Values.openrouterCatalog.excludedModels | default list -}}
{{- range $entry := .Values.openrouterCatalog.models | default list -}}
{{- if not (has $entry.upstreamModel $excluded) -}}
{{- $roles = append $roles (printf "model:%s:invoke" $entry.name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $roles | uniq | toYaml -}}
{{- end -}}
