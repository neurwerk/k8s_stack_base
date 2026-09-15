{{- define "maintenance.deployment" -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: maintenance
  namespace: maintenance
  labels: {{ .labels | toJson }}
spec:
  replicas: 1
  selector:
    matchLabels: {{ .labels | toJson }}
  template:
    metadata:
      labels: {{ .labels | toJson }}
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: maintenance
          image: {{ .values.maintenance.image | quote }}
          command: [maintenance-server]
          ports:
            - name: http
              containerPort: 8080
          env:
            - name: MAINTENANCE_COMPANY_NAME
              value: {{ .values.authKeycloak.realmDisplayName | quote }}
            - name: MAINTENANCE_LOGO_PATH
              value: /branding/company-logo.{{ .values.authKeycloak.branding.logoFormat }}
            - name: MAINTENANCE_RETRY_AFTER
              value: "300"
          readinessProbe:
            httpGet:
              path: /_maintenance/healthz
              port: 8080
          livenessProbe:
            httpGet:
              path: /_maintenance/healthz
              port: 8080
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: [ALL]
          resources: {{ .values.maintenance.resources | toJson }}
          volumeMounts:
            - name: branding
              mountPath: /branding
              readOnly: true
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: branding
          configMap:
            name: {{ .values.authKeycloak.branding.logoConfigMapName | quote }}
            items:
              - key: company-logo.{{ .values.authKeycloak.branding.logoFormat }}
                path: company-logo.{{ .values.authKeycloak.branding.logoFormat }}
        - name: tmp
          emptyDir:
            sizeLimit: 32Mi
{{- end -}}
