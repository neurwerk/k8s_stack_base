{{/* Called only after raw cardinality and the SP/HTAB-bounded vocabulary check. */}}
{{- define "infra-agentgateway.selectedCredentialSource" -}}
(request.headers["x-neurwerk-credential-mode"].matches("^[ \\t]*global[ \\t]*$") ? "global" : request.headers["x-neurwerk-credential-mode"].matches("^[ \\t]*none[ \\t]*$") ? "none" : "override")
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
"standard_oidc": has(jwt.sub) && !("x-neurwerk-authorization" in request.headers),
{{- if eq .mode "api" }}
"provider_credential_source": "x-neurwerk-credential-mode" in request.headers ? {{ include "infra-agentgateway.selectedCredentialSource" . }} : null,
"stripped_credential_headers": "x-neurwerk-credential-mode" in request.headers ?
{{- if .discovery }}
["authorization", "x-api-key", "x-goog-api-key", "api-key"].filter(h, h in request.headers)
{{- else }}
(["x-neurwerk-credential-mode"] + (({{ include "infra-agentgateway.selectedCredentialSource" . }}) in ["global", "none"] && "authorization" in request.headers ? ["authorization"] : []))
{{- end }} : [],
{{- end }}
"provider_headers": ["authorization", "x-api-key", "x-goog-api-key", "api-key"].filter(h,
h in request.headers && !(h == "authorization" && (has(jwt.sub) && !("x-neurwerk-authorization" in request.headers)
{{- if eq .mode "api" }} || ("x-neurwerk-credential-mode" in request.headers && ({{ include "infra-agentgateway.selectedCredentialSource" . }}) in ["global", "none"]){{- end }}))),
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
{{- if and (eq .entry.mode "api") (hasKey $binding "apiPath") }}
"api_path_prefix": {{ $binding.prefix | toJson }},
{{- end }}
"protocol_profile": {{ .operation.profile | toJson }}, "operation_id": {{ .operation.id | toJson }},
"provider_credential_source": ({{ include "infra-agentgateway.routeCredentialSource" $binding }}),
"policy_revision": metadata.neurwerk_identity.policy_revision}
{{- end -}}

{{- define "infra-agentgateway.routeCredentialSource" -}}
(has(metadata.neurwerk_identity.provider_credential_source) && metadata.neurwerk_identity.provider_credential_source != null ?
metadata.neurwerk_identity.provider_credential_source : (
{{- if .local -}}
"none"
{{- else -}}
{{- $missing := ternary "global" "override" (has "global" .credentialSources) -}}
metadata.neurwerk_identity.standard_oidc ? {{ $missing | toJson }} :
({{ .credentialHeader | toJson }} in metadata.neurwerk_identity.provider_headers ? "override" : {{ $missing | toJson }})
{{- end -}}
))
{{- end -}}

{{- define "infra-agentgateway.routeOperationCondition" -}}
{{- $paths := list -}}
{{- range $path := ternary (.operation.proxyPaths | default .operation.paths) .operation.paths (eq .entry.mode "forward_proxy") -}}
{{- if eq $.entry.mode "api" }}{{ $path = printf "%s%s" $.binding.prefix $path }}{{ end -}}
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
