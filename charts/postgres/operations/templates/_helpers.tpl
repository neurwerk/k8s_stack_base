{{- define "postgres-operations.labels" -}}
app.kubernetes.io/name: postgres-operations
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: postgres
{{- end }}

{{- define "postgres-operations.selectorLabels" -}}
app.kubernetes.io/name: postgres-operations
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
