{{- define "studio.collectorContainer" -}}
name: usage-collector
image: {{ required "Usage collector requires a published, pinned image" .Values.frontendStudio.usage.collectorImage | quote }}
imagePullPolicy: IfNotPresent
command: ["k8s-stack-studio-collector"]
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
resources:
  {{- toYaml .Values.frontendStudio.usage.resources | nindent 2 }}
env:
  - name: STUDIO_COLLECTOR_POSTGRES_HOST
    value: {{ .Values.frontendStudio.api.postgres.host | quote }}
  - name: STUDIO_COLLECTOR_POSTGRES_PORT
    value: {{ .Values.frontendStudio.api.postgres.port | quote }}
  - name: STUDIO_COLLECTOR_POSTGRES_PASSWORD
    valueFrom:
      secretKeyRef: {name: frontend-studio-usage-secret, key: collector-password}
  - name: STUDIO_COLLECTOR_TIMEZONE
    value: {{ .Values.frontendStudio.api.usageTimezone | quote }}
  - name: STUDIO_COLLECTOR_AGENTGATEWAY_URL
    value: {{ .Values.frontendStudio.api.agentgatewayAdminUrl | quote }}
  - name: STUDIO_COLLECTOR_OPENSEARCH_URL
    value: {{ .Values.frontendStudio.api.opensearchUrl | quote }}
  - name: STUDIO_COLLECTOR_OPENSEARCH_USER
    value: {{ .Values.frontendStudio.api.opensearchUser | quote }}
  - name: STUDIO_COLLECTOR_OPENSEARCH_PASSWORD
    valueFrom:
      secretKeyRef: {name: frontend-studio-openbao-secret, key: OPENSEARCH_PASSWORD}
  - name: STUDIO_COLLECTOR_OPENSEARCH_CA_CERT
    value: {{ .Values.frontendStudio.api.opensearchCaCert | quote }}
  - name: STUDIO_COLLECTOR_PRICING_FILE
    value: /etc/studio-usage/pricing.json
volumeMounts:
  - name: opensearch-ca
    mountPath: /certs
    readOnly: true
  - name: usage-pricing
    mountPath: /etc/studio-usage
    readOnly: true
{{- end -}}
