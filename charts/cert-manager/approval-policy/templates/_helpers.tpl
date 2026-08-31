{{- define "cert-manager-approval-policy.labels" -}}
app.kubernetes.io/name: cert-manager-approval-policy
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: infra-cert-manager
{{- end }}

{{- define "cert-manager-approval-policy.validHostname" -}}
{{- $hostname := . | default "" | trim -}}
{{- if and $hostname (ne $hostname "place.holder") (not (hasSuffix ".place.holder" $hostname)) -}}true{{- else -}}false{{- end -}}
{{- end }}

{{- define "cert-manager-approval-policy.public" -}}
apiVersion: policy.cert-manager.io/v1alpha1
kind: CertificateRequestPolicy
metadata:
  name: {{ .name }}
  labels:
    {{- include "cert-manager-approval-policy.labels" .root | nindent 4 }}
spec:
  allowed:
    dnsNames:
      values:
        - {{ .hostname | quote }}
      required: true
    isCA: false
    # approver-policy cannot mark usages required; this bounds values present.
    usages:
      - digital signature
      - key encipherment
  constraints:
    minDuration: 2160h
    maxDuration: 2160h
    privateKey:
      algorithm: RSA
      minSize: 2048
      maxSize: 2048
  selector:
    issuerRef:
      group: cert-manager.io
      kind: ClusterIssuer
      name: {{ .issuer | quote }}
    namespace:
      matchNames:
        - {{ .namespace }}
{{- end }}
