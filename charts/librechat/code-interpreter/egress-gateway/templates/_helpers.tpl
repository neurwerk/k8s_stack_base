{{- define "librechat-code-interpreter-egress-gateway.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "librechat-code-interpreter-egress-gateway.fullname" -}}
{{- default (include "librechat-code-interpreter-egress-gateway.name" .) .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "librechat-code-interpreter-egress-gateway.selectorLabels" -}}
app.kubernetes.io/name: {{ include "librechat-code-interpreter-egress-gateway.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "librechat-code-interpreter-egress-gateway.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "librechat-code-interpreter-egress-gateway.selectorLabels" . }}
app.kubernetes.io/part-of: librechat-code-interpreter
app.kubernetes.io/component: egress-gateway
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
