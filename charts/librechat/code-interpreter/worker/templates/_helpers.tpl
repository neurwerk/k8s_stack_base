{{- define "librechat-code-interpreter-worker.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "librechat-code-interpreter-worker.fullname" -}}
{{- default (include "librechat-code-interpreter-worker.name" .) .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "librechat-code-interpreter-worker.selectorLabels" -}}
app.kubernetes.io/name: {{ include "librechat-code-interpreter-worker.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "librechat-code-interpreter-worker.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "librechat-code-interpreter-worker.selectorLabels" . }}
app.kubernetes.io/part-of: librechat-code-interpreter
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "librechat-code-interpreter-worker.serviceWorkerSelectorLabels" -}}
{{ include "librechat-code-interpreter-worker.selectorLabels" . }}
app.kubernetes.io/component: service-worker
{{- end -}}

{{- define "librechat-code-interpreter-worker.sandboxSelectorLabels" -}}
{{ include "librechat-code-interpreter-worker.selectorLabels" . }}
app.kubernetes.io/component: sandbox-runner
{{- end -}}
