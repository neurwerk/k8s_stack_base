{{- define "frontend-librechat.name" -}}
frontend-librechat
{{- end }}

{{- define "frontend-librechat.selectorLabels" -}}
app.kubernetes.io/name: {{ include "frontend-librechat.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "frontend-librechat.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "frontend-librechat.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: frontend-librechat
app.kubernetes.io/component: app
{{- end }}

{{- define "frontend-librechat.serviceAccountName" -}}
frontend-librechat-service-account
{{- end }}
