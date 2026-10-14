{{- define "agentgateway-inspection-trust.labels" -}}
app.kubernetes.io/name: agentgateway-inspection-trust
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: agentgateway
{{- end }}
{{- define "agentgateway-inspection-trust.inventory" -}}
{{- $roots := list -}}
{{- range .Values.agentgateway.inspection.trust.roots -}}
{{- $roots = append $roots (dict "config_map" (printf "agentgateway-inspection-root-%s-config-map" .id) "key" "ca.crt" "fingerprint_sha256" .fingerprintSha256 "state" .state) -}}
{{- end -}}
{{- dict "schema_version" 1 "roots" $roots | toJson -}}
{{- end -}}
