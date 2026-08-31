{{- define "frontend-librechat-meilisearch.name" -}}
frontend-librechat-meilisearch
{{- end }}

{{- define "frontend-librechat-meilisearch.selectorLabels" -}}
app.kubernetes.io/name: {{ include "frontend-librechat-meilisearch.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "frontend-librechat-meilisearch.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "frontend-librechat-meilisearch.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: frontend-librechat
app.kubernetes.io/component: search
{{- end }}

{{- define "frontend-librechat-meilisearch.serviceAccountName" -}}
frontend-librechat-meilisearch-service-account
{{- end }}
