{{/* Return availability only. Secret bytes never enter effective bindings or revisions. */}}
{{- define "infra-agentgateway.companyCredential" -}}
{{- $secrets := .root.Values.infraAgentgatewayWrapperSecrets -}}
{{- $inputs := dict -}}
{{- if hasKey $secrets "providerCredentials" }}{{ $inputs = $secrets.providerCredentials }}{{ end -}}
{{- if not (kindIs "map" $inputs) }}{{ fail "providerCredentials must be a Secret-name to credential map" }}{{ end -}}
{{- $value := get $inputs .name -}}
{{- $present := hasKey $inputs .name -}}
{{- if eq .name "infra-agentgateway-secret" }}{{ $value = $secrets.openrouterApiKey }}{{ $present = hasKey $secrets "openrouterApiKey" }}{{ end -}}
{{- if eq .name "infra-agentgateway-deepseek-secret" }}{{ $value = $secrets.deepseekApiKey }}{{ $present = hasKey $secrets "deepseekApiKey" }}{{ end -}}
{{- if and (hasKey $inputs .name) (has .name (list "infra-agentgateway-secret" "infra-agentgateway-deepseek-secret")) }}{{ fail "providerCredentials conflicts with a reserved company credential input" }}{{ end -}}
{{- $available := false -}}
{{- if $present -}}
{{- if not (kindIs "string" $value) }}{{ fail "company credential input must be a string" }}{{ end -}}
{{- if ne $value "" -}}
{{- if or (not (regexMatch `^[A-Za-z0-9._~+/=-]+$` $value)) (gt (len $value) 8192) }}{{ fail "company credential input has invalid structure" }}{{ end -}}
{{- $available = true -}}
{{- end -}}
{{- end -}}
{{- dict "available" $available | toJson -}}
{{- end -}}
