{{- define "addon-oidc.validateAccess" -}}
{{- $a := .Values.authentication -}}
{{- $approval := .Values.clientApproval -}}
{{- $reserved := list "agentgateway" "studio" "keycloak-api-key-bridge" "account" "account-console" "admin-cli" "broker" "realm-management" "security-admin-console" -}}
{{- if or (has $a.clientId $reserved) (and (not (empty .Values.authKeycloak.studioClientId)) (eq $a.clientId .Values.authKeycloak.studioClientId)) }}{{ fail "reserved authentication client ID" }}{{ end -}}
{{- if ne .Release.Namespace "auth-keycloak" }}{{ fail "addon-oidc must run in auth-keycloak" }}{{ end -}}
{{- if or (empty $a.clientId) (ne $a.clientId $approval.clientId) }}{{ fail "clientApproval must explicitly approve the authentication clientId" }}{{ end -}}
{{- if or (empty $a.redirectUris) (empty $a.webOrigins) (empty $a.realmRoles) }}{{ fail "authentication requires redirectUris, webOrigins and realmRoles" }}{{ end -}}
{{- range $a.realmRoles -}}
{{- if not (has . $approval.realmRoles) }}{{ fail "authentication realmRoles require exact client approval" }}{{ end -}}
{{- end -}}
{{- range $a.audiences -}}
{{- if not (has . $approval.audiences) }}{{ fail "authentication audiences require exact client approval" }}{{ end -}}
{{- end -}}
{{- range $field := list "redirectUris" "webOrigins" -}}
{{- range $url := index $a $field -}}
{{- $parsed := urlParse $url -}}
{{- if or (ne $parsed.scheme "https") (empty $parsed.host) (not (empty $parsed.userinfo)) (not (empty $parsed.fragment)) (regexMatch "[[:space:]*\\\\]" $url) }}{{ fail "authentication requires exact HTTPS URLs" }}{{ end -}}
{{- if and (eq $field "webOrigins") (or (not (empty $parsed.path)) (not (empty $parsed.query))) }}{{ fail "webOrigins must not contain a path or query" }}{{ end -}}
{{- end -}}
{{- end -}}
{{- if has (first (splitList "." $a.roleClaim)) (list "sub" "aud" "azp" "iss" "exp" "iat" "nbf" "resource_access") }}{{ fail "reserved authentication roleClaim" }}{{ end -}}
{{- if eq $a.mode "login-only" -}}
{{- if or (not (empty $a.gatewayPermissions)) (has "agentgateway" $a.audiences) }}{{ fail "login-only cannot request gateway access" }}{{ end -}}
{{- else -}}
{{- if or (not $a.userTokenForwarding) (not (has "agentgateway" $a.audiences)) }}{{ fail "login+gateway requires explicit app user-token forwarding and agentgateway audience" }}{{ end -}}
{{- end -}}
{{- end -}}

{{- define "addon-oidc.validate" -}}
{{- include "addon-oidc.validateAccess" . -}}
{{- $image := .Values.k8sTools.image -}}
{{- if not (regexMatch "^ghcr.io/neurwerk/k8s-stack-tooling:[0-9]+\\.[0-9]+\\.[0-9]+@sha256:[a-f0-9]{64}$" $image) }}{{ fail "enable only with an explicitly published immutable tooling image" }}{{ end -}}
{{- $version := index (splitList "@" (trimPrefix "ghcr.io/neurwerk/k8s-stack-tooling:" $image)) 0 -}}
{{- if not (semverCompare ">=0.11.1" $version) }}{{ fail "addon-oidc requires tooling >=0.11.1" }}{{ end -}}
{{- end -}}

{{- define "addon-oidc.gatewayScope" -}}
{{- $roles := list -}}
{{- if eq .Values.authentication.mode "login+gateway" -}}
{{- range .Values.authentication.gatewayPermissions -}}
{{- if has . $.Values.clientApproval.gatewayPermissions }}{{ $roles = append $roles . }}{{ end -}}
{{- end -}}
{{- end -}}
{{- $roles | toJson -}}
{{- end -}}
