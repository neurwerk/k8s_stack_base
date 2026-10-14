{{- define "agentgateway-inspection-pki.labels" -}}
app.kubernetes.io/name: agentgateway-inspection-pki
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: agentgateway
{{- end }}
