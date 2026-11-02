{{- define "infra-agentgateway.identityHeader" -}}
{{- $paths := list `request.path.startsWith("/override/")` -}}
{{- range include "infra-agentgateway.aiBindings" . | fromJsonArray -}}
{{- range .apiEntries -}}
{{- if and (eq .source "override") (not (hasPrefix "/override/" .prefix)) }}{{ $paths = append $paths (printf "request.path.startsWith(%q)" (printf "%s/" .prefix)) }}{{ end -}}
{{- end -}}
{{- end -}}
(({{ join " || " $paths | default "false" }}) ? "authorization-neurwerk" : "authorization")
{{- end -}}

{{- define "infra-agentgateway.validBearer" -}}
({{ . }} in request.headers && size(request.headers.raw()[{{ . }}]) == 1 && request.headers[{{ . }}].matches("^[Bb][Ee][Aa][Rr][Ee][Rr] [A-Za-z0-9._~+/-]+=*$"))
{{- end -}}

{{- define "infra-agentgateway.validProvider" -}}
({{ . | toJson }} in request.headers && size(request.headers.raw()[{{ . | toJson }}]) == 1 && request.headers[{{ . | toJson }}].matches({{ ternary "^[Bb][Ee][Aa][Rr][Ee][Rr] [A-Za-z0-9._~+/-]+=*$" "^[A-Za-z0-9._~+/-]+=*$" (eq . "authorization") | toJson }}))
{{- end -}}

{{/* All request-varying inputs are frozen before credential removal. */}}
{{- define "infra-agentgateway.sharedIdentity" -}}
{"contract_version": 1, "request_id": has(jwt.sub) ? uuid() : extauthz.request_id,
"principal_id": has(jwt.sub) ? jwt.sub : extauthz.principal_id,
"app_id": has(jwt.sub) && has(jwt.azp) ? jwt.azp : null,
"credential_context_version": has(jwt.sub) ? null : "1",
"credential_id": has(jwt.sub) ? null : extauthz.credential_id,
"credential_kind": has(jwt.sub) ? null : extauthz.credential_kind,
"permissions": has(jwt.sub) ? jwt.resource_access.agentgateway.roles : extauthz.permissions,
"account_email": has(jwt.sub) ? (has(jwt.email) ? jwt.email : null) : extauthz.account_email,
"entry_mode": {{ .mode | toJson }}, "path": request.path, "method": request.method,
"standard_oidc": has(jwt.sub),
"provider_credential_source": {{ .source | toJson }},
"stripped_credential_headers": ["x-neurwerk-authorization", "x-neurwerk-credential-mode", "proxy-authorization", "authorization-neurwerk", "authorization", "x-api-key", "x-goog-api-key", "api-key"].filter(h, h in request.headers && h != {{ .identityHeader | toJson }} && h != {{ .preserve | toJson }})
{{- if eq .mode "forward_proxy" }} + ["x-neurwerk-authorization", "x-neurwerk-credential-mode", "authorization-neurwerk", "authorization"].filter(h, h in source.connectHeaders){{ end }},
"provider_headers": {{ if .preserve }}[{{ .preserve | toJson }}]{{ else }}[]{{ end }},
"provider_header_counts": {
{{- range $index, $header := list "authorization" "x-api-key" "x-goog-api-key" "api-key" }}
{{ if $index }},{{ end }}{{ $header | toJson }}: {{ $header | toJson }} in request.headers ? size(request.headers.raw()[{{ $header | toJson }}]) : 0
{{- end }}}, "policy_revision": {{ .revision | toJson }}}
{{- end -}}

{{- define "infra-agentgateway.routeAdmissionContext" -}}
{{- $binding := .binding -}}
{"request_id": metadata.neurwerk_identity.request_id,
"principal_id": metadata.neurwerk_identity.principal_id,
"credential_context_version": metadata.neurwerk_identity.credential_context_version,
"credential_id": metadata.neurwerk_identity.credential_id,
"credential_kind": metadata.neurwerk_identity.credential_kind,
"permissions": metadata.neurwerk_identity.permissions,
"entry_mode": {{ .entry.mode | toJson }},
"destination_kind": {{ $binding.kind | default "model" | toJson }},
"provider_id": {{ $binding.providerId | toJson }}, "binding_id": {{ $binding.id | toJson }},
{{- if and (eq .entry.mode "api") (ne ($binding.kind | default "model") "mcp") }}
"api_path_prefix": {{ .entry.prefix | toJson }},
{{- end }}
"protocol_profile": {{ .operation.profile | toJson }}, "operation_id": {{ .operation.id | toJson }},
"provider_credential_source": ({{ include "infra-agentgateway.routeCredentialSource" $binding }}),
"policy_revision": metadata.neurwerk_identity.policy_revision}
{{- end -}}

{{- define "infra-agentgateway.routeCredentialSource" -}}
metadata.neurwerk_identity.provider_credential_source
{{- end -}}

{{- define "infra-agentgateway.routeOperationCondition" -}}
{{- $paths := list -}}
{{- range $path := ternary (.operation.proxyPaths | default .operation.paths) .operation.paths (eq .entry.mode "forward_proxy") -}}
{{- if eq $.entry.mode "api" }}{{ $path = printf "%s%s" $.entry.prefix $path }}{{ end -}}
{{- $paths = append $paths $path -}}
{{- end -}}
metadata.neurwerk_identity.path in {{ $paths | toJson }} &&
metadata.neurwerk_identity.method in {{ ternary (list "POST" "GET" "DELETE") (list "POST") (eq (.binding.kind | default "model") "mcp") | toJson }}
{{- end -}}

{{- define "infra-agentgateway.routeClientAuthorization" -}}
{{- $matches := list -}}
{{- range .root.Values.aiGateway.approvedOidcClients -}}
{{- if has $.binding.id .bindings }}{{ $matches = append $matches (printf "jwt.azp == %q" .clientId) }}{{ end -}}
{{- end -}}
(!has(jwt.sub) || (has(jwt.azp) && (jwt.azp == "agentgateway"
{{- if eq (.binding.kind | default "model") "mcp" }} ||
(jwt.azp == {{ .root.Values.authKeycloak.studioClientId | default "studio" | toJson }} && has(jwt.realm_access.roles) && "studio-user" in jwt.realm_access.roles)
{{- end }} ||
(has(jwt.aud) && ((type(jwt.aud) == string && jwt.aud == "agentgateway") ||
(type(jwt.aud) == list && "agentgateway" in jwt.aud)) && ({{ join " || " $matches | default "false" }})))))
{{- end -}}

{{- define "infra-agentgateway.routeIdentityAuthorization" -}}
has(metadata.neurwerk_identity) && metadata.neurwerk_identity.contract_version == 1 &&
metadata.neurwerk_identity.policy_revision == {{ include "infra-agentgateway.aiRevision" .root | toJson }} &&
metadata.neurwerk_identity.entry_mode == {{ .entry.mode | toJson }} &&
type(metadata.neurwerk_identity.principal_id) == string && size(metadata.neurwerk_identity.principal_id) > 0 &&
"llm:invoke" in metadata.neurwerk_identity.permissions &&
({{ include "infra-agentgateway.routeClientAuthorization" . }})
{{- end -}}

{{- define "infra-agentgateway.routeAuthorization" -}}
{{- $conditions := list -}}
{{- range .binding.operations -}}
{{- $conditions = append $conditions (printf "(%s)" (include "infra-agentgateway.routeOperationCondition" (dict "binding" $.binding "entry" $.entry "operation" .))) -}}
{{- end -}}
({{ include "infra-agentgateway.routeIdentityAuthorization" . }}) && ({{ join " || " $conditions }}) &&
{{- if ne (.binding.kind | default "model") "mcp" }}
{{- if eq .entry.mode "api" }}
has(metadata.neurwerk_identity.provider_credential_source) && metadata.neurwerk_identity.provider_credential_source != null &&
metadata.neurwerk_identity.provider_credential_source == {{ .entry.source | toJson }} &&
{{- end }}
{{- end }}
{{- if .binding.local }}
size(metadata.neurwerk_identity.provider_headers) == 0 &&
({{ include "infra-agentgateway.routeCredentialSource" .binding }}) == "none"
{{- else }}
metadata.neurwerk_identity.provider_headers.all(h, h == {{ .binding.credentialHeader | toJson }} && metadata.neurwerk_identity.provider_header_counts[h] == 1) &&
({{ include "infra-agentgateway.routeCredentialSource" .binding }}) in {{ .binding.credentialSources | toJson }} &&
(({{ include "infra-agentgateway.routeCredentialSource" .binding }}) == "global" ?
{{ printf "provider:%s:global-use" .binding.providerId | toJson }} in metadata.neurwerk_identity.permissions :
{{ .binding.credentialHeader | toJson }} in metadata.neurwerk_identity.provider_headers)
{{- end }}
{{- if eq (.binding.kind | default "model") "mcp" }} &&
{{ .binding.requiredPermission | toJson }} in metadata.neurwerk_identity.permissions
{{- end -}}
{{- end -}}
