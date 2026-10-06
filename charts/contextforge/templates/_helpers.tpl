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
