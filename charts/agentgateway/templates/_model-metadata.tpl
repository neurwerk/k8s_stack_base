{{/* Native CEL expands compact rows to the unchanged extProc v6 contract.
Keep the actual expression bounded, not the expanded runtime metadata map. */}}
{{- define "infra-agentgateway.modelBindingsExpression" -}}
{{- if le (len (toJson .modelBindings)) 16384 -}}
{{- .modelBindings | toJson -}}
{{- else -}}
{{- $names := keys .modelBindings | sortAlpha -}}
{{- $rows := list -}}
{{- range $index, $name := $names -}}
{{- $model := get $.modelBindings $name -}}
{{- $permissions := list -}}
{{- range $permission := $model.required_permissions -}}
{{- $permissions = append $permissions (ternary "null" (toJson $permission) (eq $permission (printf "model:%s:invoke" $name))) -}}
{{- end -}}
{{- $rows = append $rows (printf `names[%d]:[%s,[%s],%s,%s,%d,%s]` $index (toJson $model.upstream_model) (join "," $permissions) (toJson $model.content_tracing_enabled) (toJson ($model.allowed_reroutes | default list)) $index (toJson $model.normalize_stream_created)) -}}
{{- end -}}
{{- $expression := printf `%s.with(names, {%s}.mapValues(v, {"upstream_model": v[0], "required_permissions": v[1].map(p, p == null ? "model:" + names[v[4]] + ":invoke" : p), "content_tracing_enabled": v[2], "allowed_reroutes": v[3], "normalize_stream_created": v[5]}))` (toJson $names) (join "," $rows) -}}
{{- if gt (len $expression) 16384 }}{{ fail "model bindings exceed native 16384-character expression limit" }}{{ end -}}
{{- $expression -}}
{{- end -}}
{{- end -}}

{{- define "infra-agentgateway.destinationPolicyExpression" -}}
{{- $pii := dict -}}
{{- range .models }}{{ $_ := set $pii .name .piiEnabled }}{{ end -}}
{{- $inner := merge (dict "contract_version" "4.1" "destination_kind" "model" "models" $pii) (deepCopy .attachmentPolicy) -}}
{{- $literal := printf `%s.merge({"principal_id": metadata.neurwerk_identity.principal_id, "credential_context_version": metadata.neurwerk_identity.credential_context_version, "credential_id": metadata.neurwerk_identity.credential_id, "credential_kind": metadata.neurwerk_identity.credential_kind})` (toJson $inner) -}}
{{- if le (len $literal) 16384 -}}
{{- $literal -}}
{{- else -}}
{{- $fields := keys (omit .attachmentPolicy "pdf_modes" "pdf_fallbacks" "pdf_max_rendered_pages") | sortAlpha -}}
{{- $rows := dict -}}
{{- range $model := .models -}}
{{- $row := list $model.piiEnabled -}}
{{- range $field := $fields }}{{ $row = append $row (get (get $.attachmentPolicy $field) $model.name) }}{{ end -}}
{{- $_ := set $rows $model.name $row -}}
{{- end -}}
{{- $parts := list `"models": rows.mapValues(v, v[0])` -}}
{{- range $field := list "pdf_modes" "pdf_fallbacks" "pdf_max_rendered_pages" -}}
{{- if hasKey $.attachmentPolicy $field -}}
{{- $parts = append $parts (printf `%q: %s` $field (toJson (get $.attachmentPolicy $field))) -}}
{{- end -}}
{{- end -}}
{{- range $index, $field := $fields -}}
{{- $parts = append $parts (printf `%q: rows.mapValues(v, v[%d])` $field (add1 $index)) -}}
{{- end -}}
{{- $expression := printf `%s.with(rows, {"contract_version": "4.1", "destination_kind": "model", %s, "principal_id": metadata.neurwerk_identity.principal_id, "credential_context_version": metadata.neurwerk_identity.credential_context_version, "credential_id": metadata.neurwerk_identity.credential_id, "credential_kind": metadata.neurwerk_identity.credential_kind})` (toJson $rows) (join ", " $parts) -}}
{{- if gt (len $expression) 16384 }}{{ fail "destination policy exceeds native 16384-character expression limit" }}{{ end -}}
{{- $expression -}}
{{- end -}}
{{- end -}}
