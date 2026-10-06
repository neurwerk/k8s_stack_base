{{/* Native private endpoint: never use system roots or skip verification. */}}
{{- define "infra-agentgateway.contextforgeTLS" -}}
tls:
  sni: contextforge.contextforge.svc.cluster.local
  verifySubjectAltNames:
    - contextforge.contextforge.svc.cluster.local
  caCertificateRefs:
    - kind: ConfigMap
      name: infra-openbao-ca-bundle
      key: ca.crt
{{- end -}}
