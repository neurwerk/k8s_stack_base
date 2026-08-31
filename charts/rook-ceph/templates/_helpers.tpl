{{- define "infra-rook-ceph.labels" -}}
app.kubernetes.io/name: rook-ceph
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: {{ .Release.Namespace }}
{{- end }}
