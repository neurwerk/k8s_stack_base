{{- define "librechat-code-interpreter-package-init.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "librechat-code-interpreter-package-init.fullname" -}}
{{- default (include "librechat-code-interpreter-package-init.name" .) .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "librechat-code-interpreter-package-init.selectorLabels" -}}
app.kubernetes.io/name: {{ include "librechat-code-interpreter-package-init.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "librechat-code-interpreter-package-init.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "librechat-code-interpreter-package-init.selectorLabels" . }}
app.kubernetes.io/part-of: librechat-code-interpreter
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
