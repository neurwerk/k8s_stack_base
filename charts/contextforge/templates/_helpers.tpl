{{- define "contextforge.labels" -}}
app.kubernetes.io/name: contextforge
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: contextforge
{{- end -}}

{{- define "contextforge.credentials" -}}
{{- range list "DATABASE_URL" "JWT_SECRET_KEY" "AUTH_ENCRYPTION_SECRET" "PLATFORM_ADMIN_EMAIL" "PLATFORM_ADMIN_PASSWORD" "DEFAULT_USER_PASSWORD" "VAULT_TOKEN" }}
- name: {{ . }}
  valueFrom:
    secretKeyRef:
      name: {{ $.Values.contextforge.existingSecret }}
      key: {{ . }}
{{- end }}
{{- end -}}

{{- define "contextforge.trustInit" -}}
- name: prepare-ca-trust
  image: {{ .Values.contextforge.image | quote }}
  imagePullPolicy: IfNotPresent
  command: [python3, -c]
  args:
    - |
      from pathlib import Path
      import certifi
      import ssl
      public = Path(certifi.where()).read_bytes()
      internal = Path('/openbao-ca/ca.crt').read_bytes()
      # Verify both inputs before exposing the combined trust to HTTPX.
      ssl.create_default_context(cadata=internal.decode())
      bundle = Path('/trust/ca.crt')
      bundle.write_bytes(public + b'\n' + internal)
      ssl.create_default_context(cafile=str(bundle))
  securityContext:
    {{- include "contextforge.containerSecurity" . | nindent 4 }}
  resources:
    requests: {cpu: 25m, memory: 64Mi}
    limits: {cpu: 100m, memory: 128Mi}
  volumeMounts:
    - {name: openbao-ca, mountPath: /openbao-ca, readOnly: true}
    - {name: trust, mountPath: /trust}
{{- end -}}

{{- define "contextforge.trustVolumes" -}}
- name: openbao-ca
  configMap:
    name: infra-openbao-ca-bundle
    items: [{key: ca.crt, path: ca.crt}]
- name: trust
  emptyDir: {sizeLimit: 2Mi}
{{- end -}}

{{- define "contextforge.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile: {type: RuntimeDefault}
{{- end -}}

{{- define "contextforge.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: {drop: [ALL]}
{{- end -}}
