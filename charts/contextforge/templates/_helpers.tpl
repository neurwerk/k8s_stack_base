{{- define "contextforge.labels" -}}
app.kubernetes.io/name: contextforge
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: contextforge
{{- end -}}

{{- define "contextforge.credentials" -}}
{{- range list "DATABASE_URL" "JWT_SECRET_KEY" "AUTH_ENCRYPTION_SECRET" "PLATFORM_ADMIN_EMAIL" "PLATFORM_ADMIN_PASSWORD" "DEFAULT_USER_PASSWORD" }}
- name: {{ . }}
  valueFrom:
    secretKeyRef:
      name: {{ $.Values.contextforge.existingSecret }}
      key: {{ . }}
{{- end }}
{{- end -}}

{{- define "contextforge.proxyEnvironment" -}}
{{- if not (kindIs "bool" .Values.mcp.studioSetup.enabled) }}{{ fail "mcp.studioSetup.enabled must be a boolean" }}{{- end }}
{{- if and .Values.mcp.studioSetup.enabled (not (and .Values.contextforge.setup.enabled .Values.contextforge.trustedProxy.enabled)) }}{{ fail "Studio MCP setup requires native bootstrap and trustedProxy.enabled" }}{{- end }}
{{- if .Values.contextforge.trustedProxy.enabled }}
{{- if not .Values.contextforge.tls.enabled }}{{ fail "ContextForge trustedProxy requires native TLS" }}{{- end }}
{{- $origin := required "ContextForge trustedProxy.studioOrigin is required" .Values.contextforge.trustedProxy.studioOrigin }}
{{- if not (regexMatch "^https://[a-zA-Z0-9.-]+(:[0-9]+)?$" $origin) }}{{ fail "ContextForge studioOrigin must be an exact HTTPS origin without a path" }}{{- end }}
{{- $origin = regexReplaceAll ":443$" (lower $origin) "" }}
- {name: TRUST_PROXY_AUTH, value: "true"}
- {name: TRUST_PROXY_AUTH_DANGEROUSLY, value: "true"}
- {name: MCP_CLIENT_AUTH_ENABLED, value: "false"}
- {name: PROXY_USER_HEADER, value: x-contextforge-account-email}
- {name: AUTH_REQUIRED, value: "true"}
- {name: MCP_REQUIRE_AUTH, value: "true"}
- {name: REQUIRE_USER_IN_DB, value: "true"}
# Private header-authenticated APIs; browser requests terminate at Studio/Gateway.
- {name: CSRF_ENABLED, value: "false"}
{{- range $tier, $key := dict "CRITICAL" "criticalRpm" "HIGH" "highRpm" "MEDIUM" "mediumRpm" "LOW" "lowRpm" }}
{{- $rpm := get $.Values.contextforge.trustedProxy.rateLimits $key }}
{{- if or (lt (int $rpm) 1) (gt (int $rpm) 10000) }}{{ fail "ContextForge private rate limits must be between 1 and 10000 RPM" }}{{- end }}
- {name: {{ printf "RATE_LIMIT_%s_RPM" $tier }}, value: {{ $rpm | quote }}}
{{- end }}
- {name: MCPGATEWAY_DIRECT_PROXY_ENABLED, value: "false"}
- {name: AUTO_CREATE_PERSONAL_TEAMS, value: "false"}
- {name: MCPGATEWAY_UI_ENABLED, value: "false"}
- {name: MCPGATEWAY_ADMIN_API_ENABLED, value: "true"}
- {name: LOG_REQUESTS, value: "false"}
- {name: DISABLE_ACCESS_LOG, value: "true"}
- {name: LOG_LEVEL, value: CRITICAL}
- {name: GUNICORN_CMD_ARGS, value: "--log-level critical"}
- {name: DEFAULT_USER_ROLE, value: {{ required "ContextForge trustedProxy.defaultUserRole is required" .Values.contextforge.trustedProxy.defaultUserRole | quote }}}
- {name: DEFAULT_TEAM_MEMBER_ROLE, value: {{ required "ContextForge trustedProxy.defaultTeamMemberRole is required" .Values.contextforge.trustedProxy.defaultTeamMemberRole | quote }}}
{{- if .Values.contextforge.setup.enabled }}
- {name: DEFAULT_TEAM_OWNER_ROLE, value: {{ .Values.contextforge.setup.ownerRoleName | quote }}}
{{- end }}
{{- if .Values.mcp.studioSetup.enabled }}
# Install destinations even before their required shared key has been entered.
- {name: GATEWAY_ASYNC_LIFECYCLE_ENABLED, value: "true"}
{{- end }}
- {name: SSRF_ALLOWED_NETWORKS, value: {{ .Values.contextforge.ssrfAllowedNetworks | toJson | quote }}}
- {name: APP_DOMAIN, value: {{ $origin | quote }}}
{{- end }}
{{- end -}}

{{- define "contextforge.trustInit" -}}
- name: prepare-ca-trust
  image: {{ .Values.contextforge.image | quote }}
  imagePullPolicy: IfNotPresent
  command: [python3, -c]
  args:
    - |
      from pathlib import Path
      import certifi
      import ssl
      public = Path(certifi.where()).read_bytes()
      internal = Path('/openbao-ca/ca.crt').read_bytes()
      # Verify both inputs before exposing the combined trust to HTTPX.
      ssl.create_default_context(cadata=internal.decode())
      bundle = Path('/trust/ca.crt')
      bundle.write_bytes(public + b'\n' + internal)
      ssl.create_default_context(cafile=str(bundle))
  securityContext:
    {{- include "contextforge.containerSecurity" . | nindent 4 }}
  resources:
    requests: {cpu: 25m, memory: 64Mi}
    limits: {cpu: 100m, memory: 128Mi}
  volumeMounts:
    - {name: openbao-ca, mountPath: /openbao-ca, readOnly: true}
    - {name: trust, mountPath: /trust}
{{- end -}}

{{- define "contextforge.trustVolumes" -}}
- name: openbao-ca
  configMap:
    name: infra-openbao-ca-bundle
    items: [{key: ca.crt, path: ca.crt}]
- name: trust
  emptyDir: {sizeLimit: 2Mi}
{{- end -}}

{{- define "contextforge.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile: {type: RuntimeDefault}
{{- end -}}

{{- define "contextforge.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: {drop: [ALL]}
{{- end -}}
