{{- define "forgejo.selectorLabels" -}}
app.kubernetes.io/name: forgejo
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "forgejo.labels" -}}
{{ include "forgejo.selectorLabels" . }}
app.kubernetes.io/part-of: forgejo
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}
{{- define "forgejo.image" -}}
code.forgejo.org/forgejo/forgejo:15.0.8-rootless@sha256:8f97b55ca162ef3b538c6c78a2a077df9f2143c41d80b2bc6b6920f8d430df34
{{- end -}}
{{- define "forgejo.securityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end -}}
{{- define "forgejo.mounts" -}}
- name: data
  mountPath: /data
- name: config
  mountPath: /run/forgejo
- name: scripts
  mountPath: /scripts
  readOnly: true
- name: tmp
  mountPath: /tmp
- name: runtime
  mountPath: /run/secrets/forgejo
  readOnly: true
- name: tls
  mountPath: /run/tls
  readOnly: true
{{- end -}}
