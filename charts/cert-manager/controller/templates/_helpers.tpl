{{- define "cert-manager-controller.labels" -}}
app.kubernetes.io/name: cert-manager-controller
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: cert-manager
{{- end -}}

{{- define "cert-manager-controller.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cert-manager.name" (index .Subcharts "cert-manager") }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: controller
{{- end -}}
