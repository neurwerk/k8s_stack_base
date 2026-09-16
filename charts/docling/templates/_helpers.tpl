{{- define "docling.labels" -}}
app.kubernetes.io/name: docling
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: docling
{{- end }}
