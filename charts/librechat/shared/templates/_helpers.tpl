{{- define "frontend-librechat-shared.name" -}}
frontend-librechat-shared
{{- end }}

{{- define "frontend-librechat-shared.selectorLabels" -}}
app.kubernetes.io/name: {{ include "frontend-librechat-shared.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "frontend-librechat-shared.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "frontend-librechat-shared.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: frontend-librechat
app.kubernetes.io/component: shared
{{- end }}
{{- define "frontend-librechat-shared.modelCatalog" -}}
{{- $catalogUpstreams := dict -}}
{{- $catalogNames := dict -}}
{{- range $entry := .Values.openrouterCatalog.models | default list -}}
{{- if hasKey $catalogUpstreams $entry.upstreamModel }}{{- fail (printf "OpenRouter upstream model %q is duplicated" $entry.upstreamModel) }}{{- end -}}
{{- if hasKey $catalogNames $entry.name }}{{- fail (printf "OpenRouter public model name %q is duplicated" $entry.name) }}{{- end -}}
{{- $_ := set $catalogUpstreams $entry.upstreamModel true -}}
{{- $_ := set $catalogNames $entry.name true -}}
{{- end -}}
{{- $excluded := .Values.openrouterCatalog.excludedModels | default list -}}
{{- if ne (len $excluded) (len ($excluded | uniq)) }}{{- fail "openrouterCatalog.excludedModels must not contain duplicates" }}{{- end -}}
{{- range $upstream := $excluded }}{{- if not (hasKey $catalogUpstreams $upstream) }}{{- fail (printf "openrouterCatalog.excludedModels contains unknown upstream model %q" $upstream) }}{{- end }}{{- end -}}
{{- $clientModels := .Values.guardrails.llmPolicyEngine.models | default list -}}
{{- $clientNames := dict -}}
{{- range $model := $clientModels -}}
{{- if hasKey $clientNames $model.name }}{{- fail (printf "client model name %q is duplicated" $model.name) }}{{- end -}}
{{- $_ := set $clientNames $model.name true -}}
{{- end -}}
{{- $specs := list -}}
{{- $names := list -}}
{{- $effectiveNames := dict -}}
{{- $defaultModel := .Values.frontendLibrechat.agentGateway.defaultModel | default "" -}}
{{- if .Values.openrouterCatalog.enabled -}}
{{- range $entry := .Values.openrouterCatalog.models | default list -}}
{{- if and (not (has $entry.upstreamModel $excluded)) (not (hasKey $clientNames $entry.name)) -}}
{{- $spec := dict "name" $entry.name "label" $entry.label "group" $entry.group "preset" (dict "endpoint" "AgentGateway" "model" $entry.name) -}}
{{- if eq $entry.name $defaultModel }}{{- $_ := set $spec "default" true }}{{- end -}}
{{- $specs = append $specs $spec -}}
{{- $names = append $names $entry.name -}}
{{- $_ := set $effectiveNames $entry.name true -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- range $model := $clientModels -}}
{{- $group := $model.group | default (printf "Remote-%s" ($model.provider | default "Custom")) -}}
{{- if $model.local }}{{- $group = "Local" }}{{- end -}}
{{- $spec := dict "name" $model.name "label" ($model.label | default $model.name) "group" $group "preset" (dict "endpoint" "AgentGateway" "model" $model.name) -}}
{{- if eq $model.name $defaultModel }}{{- $_ := set $spec "default" true }}{{- end -}}
{{- $specs = append $specs $spec -}}
{{- $names = append $names $model.name -}}
{{- $_ := set $effectiveNames $model.name true -}}
{{- end -}}
{{- if gt (len $specs) 256 }}{{- fail "effective LibreChat model catalog supports at most 256 destinations" }}{{- end -}}
{{- if and $defaultModel (not (hasKey $effectiveNames $defaultModel)) }}{{- fail (printf "frontendLibrechat.agentGateway.defaultModel %q is not in the effective model catalog" $defaultModel) }}{{- end -}}
{{- dict "names" $names "specs" $specs | toYaml -}}
{{- end -}}
