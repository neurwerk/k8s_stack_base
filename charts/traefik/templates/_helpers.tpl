{{- define "traefik.validateForwardProxy" -}}
{{- if and .Values.aiGateway.enabled .Values.aiGateway.proxy.enabled -}}
{{- if not (and (kindIs "bool" .Values.traefik.providers.kubernetesCRD.enabled) .Values.traefik.providers.kubernetesCRD.enabled) -}}
{{- fail "aiGateway.proxy requires traefik.providers.kubernetesCRD.enabled: true" -}}
{{- end -}}
{{- if ne (int .Values.aiGateway.proxy.publicPort) (int .Values.traefik.ports.websecure.exposedPort) -}}
{{- fail "aiGateway.proxy.publicPort must match traefik.ports.websecure.exposedPort" -}}
{{- end -}}
{{- if eq .Values.aiGateway.proxy.hostname .Values.infraAgentgatewayWrapper.hostname -}}
{{- fail "API and proxy hostnames must differ for shared HTTPS ingress" -}}
{{- end -}}
{{- end -}}
{{- end -}}
