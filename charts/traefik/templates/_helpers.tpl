{{- define "traefik.validateForwardProxy" -}}
{{- if and .Values.aiGateway.enabled .Values.aiGateway.proxy.enabled -}}
{{- if not (and (kindIs "bool" .Values.traefik.providers.kubernetesCRD.enabled) .Values.traefik.providers.kubernetesCRD.enabled) -}}
{{- fail "aiGateway.proxy requires traefik.providers.kubernetesCRD.enabled: true" -}}
{{- end -}}
{{- $port := int .Values.aiGateway.proxy.port -}}
{{/* K3s vendor chart 40.1.4+up40.1.0 owns the private admin/metrics ports. */}}
{{- if has $port (list 8080 9100) -}}
{{- fail "aiGateway.proxy.port collides with a Traefik admin or metrics port (8080, 9100)" -}}
{{- end -}}
{{- range $name := list "web" "websecure" -}}
{{- $existing := index $.Values.traefik.ports $name -}}
{{- if or (eq $port (int $existing.port)) (eq $port (int $existing.exposedPort)) -}}
{{- fail (printf "aiGateway.proxy.port collides with traefik.ports.%s (container or exposed/host port)" $name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
