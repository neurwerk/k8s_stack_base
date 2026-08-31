{{- define "postgres-auth.labels" -}}
app.kubernetes.io/name: postgres-auth
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: postgres
{{- end }}

{{- define "postgres-auth.selectorLabels" -}}
app.kubernetes.io/name: postgres-auth
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
