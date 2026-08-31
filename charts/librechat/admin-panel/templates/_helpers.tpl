{{- define "librechat-admin-panel.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "librechat-admin-panel.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := include "librechat-admin-panel.name" . }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "librechat-admin-panel.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "librechat-admin-panel.selectorLabels" -}}
app.kubernetes.io/name: {{ include "librechat-admin-panel.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "librechat-admin-panel.labels" -}}
helm.sh/chart: {{ include "librechat-admin-panel.chart" . }}
{{ include "librechat-admin-panel.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: frontend-librechat
app.kubernetes.io/component: admin-panel
{{- end }}

{{- define "librechat-admin-panel.validHostname" -}}
{{- $hostname := lower (trim .) -}}
{{- if and $hostname (ne $hostname "place.holder") (not (hasSuffix ".place.holder" $hostname)) -}}
true
{{- end -}}
{{- end }}

{{- define "librechat-admin-panel.publicApiUrl" -}}
{{- $hostname := .Values.frontendLibrechat.hostname | default "" | trim -}}
{{- if eq (include "librechat-admin-panel.validHostname" $hostname) "true" -}}
{{- printf "https://%s" $hostname -}}
{{- end -}}
{{- end }}

{{- define "librechat-admin-panel.secretNames" -}}
{{- $secret := .Values.frontendLibrechat.adminPanel.secret -}}
{{- $names := list -}}
{{- if $secret.existingSecret -}}
{{- $names = append $names $secret.existingSecret -}}
{{- else -}}
{{- with $secret.session.secretName -}}
{{- $names = append $names . -}}
{{- end -}}
{{- with $secret.metrics.secretName -}}
{{- $names = append $names . -}}
{{- end -}}
{{- end -}}
{{- join "," (uniq $names) -}}
{{- end }}

{{- define "librechat-admin-panel.metricsSecretName" -}}
{{- $secret := .Values.frontendLibrechat.adminPanel.secret -}}
{{- if $secret.existingSecret -}}
{{- $secret.existingSecret -}}
{{- else -}}
{{- $secret.metrics.secretName -}}
{{- end -}}
{{- end }}

{{- define "librechat-admin-panel.tlsSecretName" -}}
{{- printf "%s-tls" (include "librechat-admin-panel.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end }}
