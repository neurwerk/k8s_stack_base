{{- define "frontend-librechat-rag-api.name" -}}
frontend-librechat-rag-api
{{- end }}

{{- define "frontend-librechat-rag-api.selectorLabels" -}}
app.kubernetes.io/name: {{ include "frontend-librechat-rag-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "frontend-librechat-rag-api.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "frontend-librechat-rag-api.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: frontend-librechat
app.kubernetes.io/component: rag-api
{{- end }}

{{- define "frontend-librechat-rag-api.serviceAccountName" -}}
frontend-librechat-rag-api-service-account
{{- end }}
