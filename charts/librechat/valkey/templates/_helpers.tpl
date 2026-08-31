{{- define "frontend-librechat-valkey.name" -}}
frontend-librechat-valkey
{{- end }}

{{- define "frontend-librechat-valkey.selectorLabels" -}}
app.kubernetes.io/name: {{ include "frontend-librechat-valkey.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "frontend-librechat-valkey.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "frontend-librechat-valkey.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: frontend-librechat
app.kubernetes.io/component: cache
{{- end }}

{{- define "frontend-librechat-valkey.serviceAccountName" -}}
frontend-librechat-valkey-service-account
{{- end }}
