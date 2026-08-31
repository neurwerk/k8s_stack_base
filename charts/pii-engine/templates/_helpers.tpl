{{- define "monitor-pii-engine.labels" -}}
app.kubernetes.io/name: pii-engine
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: monitor-pii-engine
{{- end }}

{{- define "monitor-pii-engine.valkeySelectorLabels" -}}
app.kubernetes.io/name: valkey
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: primary
{{- end }}

{{- define "monitor-pii-engine.valkeyLabels" -}}
{{ include "monitor-pii-engine.valkeySelectorLabels" . }}
app.kubernetes.io/part-of: monitor-pii-engine
app.kubernetes.io/version: "9.1.1"
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}
