{{/*
Expand the name of the chart.
*/}}
{{- define "auth-keycloak-api-key-bridge.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "auth-keycloak-api-key-bridge.fullname" -}}
{{- printf "%s-%s" .Release.Namespace .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "auth-keycloak-api-key-bridge.labels" -}}
helm.sh/chart: {{ include "auth-keycloak-api-key-bridge.name" . }}-{{ .Chart.Version | replace "+" "_" }}
{{ include "auth-keycloak-api-key-bridge.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: {{ .Release.Namespace }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "auth-keycloak-api-key-bridge.selectorLabels" -}}
app.kubernetes.io/name: {{ include "auth-keycloak-api-key-bridge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
