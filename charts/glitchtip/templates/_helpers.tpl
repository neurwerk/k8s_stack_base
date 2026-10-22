{{- define "glitchtip.labels" -}}
app.kubernetes.io/name: glitchtip
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "glitchtip.securityContext" -}}
runAsNonRoot: true
runAsUser: 5000
runAsGroup: 5000
fsGroup: 5000
seccompProfile:
  type: RuntimeDefault
{{- end -}}
