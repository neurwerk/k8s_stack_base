{{- define "pii-engine-model-sync.labels" -}}
app.kubernetes.io/name: pii-engine-model-sync
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: monitor-pii-engine
{{- end }}
