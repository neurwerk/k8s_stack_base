{{/* One validated producer for v2 metadata and its size checks. Locality follows
the concrete routing branch, never names, groups, URLs or policy route classes. */}}
{{- define "infra-agentgateway.attachmentPolicy" -}}
{{- $engine := .Values.guardrails.llmPolicyEngine -}}
{{- $version := toJson $engine.attachmentPolicyVersion -}}
{{- if not (has $version (list "1" "2")) -}}
{{- fail "guardrails.llmPolicyEngine.attachmentPolicyVersion must be integer 1 or 2" -}}
{{- end -}}
{{- $maps := dict "image_forwarding" (dict) "face_protection" (dict) "local_models" (dict) -}}
{{- range $model := include "infra-agentgateway.effectiveModels" . | fromYamlArray -}}
{{- $name := $model.name -}}
{{- if and (eq $version "1") (or (hasKey $model "imageForwarding") (hasKey $model "faceProtectionEnabled") (eq (toString $model.attachmentMode) "process")) -}}
{{- fail (printf "model %q new attachment settings require attachmentPolicyVersion: 2 and a deployed v2 consumer" $name) -}}
{{- end -}}
{{- if eq $version "2" -}}
{{- range $field := list "local" "piiReroute" "faceProtectionEnabled" -}}
{{- if and (hasKey $model $field) (not (kindIs "bool" (get $model $field))) -}}
{{- fail (printf "model %q %s must be a boolean" $name $field) -}}
{{- end -}}
{{- end -}}
{{- $mode := $model.attachmentMode | default "block" -}}
{{- $processing := has $mode (list "extract" "process") -}}
{{- $pii := true -}}
{{- if hasKey $model "piiEnabled" }}{{- $pii = $model.piiEnabled }}{{- end -}}
{{- $face := $processing -}}
{{- if hasKey $model "faceProtectionEnabled" }}{{- $face = $model.faceProtectionEnabled }}{{- end -}}
{{- $forward := "none" -}}
{{- if hasKey $model "imageForwarding" -}}
{{- $forward = $model.imageForwarding -}}
{{- if not (and (kindIs "string" $forward) (has $forward (list "none" "if-no-pii-detected" "pii-unchecked"))) -}}
{{- fail (printf "model %q imageForwarding must be none, if-no-pii-detected, or pii-unchecked" $name) -}}
{{- end -}}
{{- end -}}
{{- $target := $.Values.infraAgentgatewayWrapper.llamacpp -}}
{{- $local := and (eq (toJson $model.local) "true") (not $model.piiReroute) (eq (toJson $target.enabled) "true") (kindIs "string" $target.host) (not (empty $target.host)) (kindIs "string" $model.model) (not (empty $model.model)) -}}
{{- if and (eq $mode "passthrough") (or $pii $face (hasKey $model "imageForwarding")) -}}
{{- fail (printf "model %q passthrough requires piiEnabled:false, faceProtectionEnabled:false and no explicit imageForwarding" $name) -}}
{{- end -}}
{{- if and (ne $forward "none") (not $processing) -}}
{{- fail (printf "model %q image forwarding requires attachmentMode process or extract" $name) -}}
{{- end -}}
{{- if and (ne $forward "none") (not (and $.Values.docling.enabled (has $.Values.docling.inference.mode (list "private-vlm" "remote")))) -}}
{{- fail (printf "model %q image forwarding requires enabled Docling private-vlm or remote mode" $name) -}}
{{- end -}}
{{- if eq $forward "if-no-pii-detected" -}}
{{- if not (and $pii $face) -}}
{{- fail (printf "model %q if-no-pii-detected requires PII and face protection" $name) -}}
{{- end -}}
{{- end -}}
{{- if and (eq $forward "pii-unchecked") (or $face (not $local)) -}}
{{- fail (printf "model %q pii-unchecked requires faceProtectionEnabled:false and a concrete local:true target without piiReroute" $name) -}}
{{- end -}}
{{- if ne $mode "passthrough" -}}
{{- $_ := set $maps.image_forwarding $name $forward -}}
{{- end -}}
{{- $_ := set $maps.face_protection $name $face -}}
{{- $_ := set $maps.local_models $name $local -}}
{{- end -}}
{{- end -}}
{{- range $field, $map := $maps -}}
{{- if gt (len (toJson $map)) 16384 -}}
{{- fail (printf "%s destination metadata JSON exceeds the 16384-byte AgentGateway limit" $field) -}}
{{- end -}}
{{- end -}}
{{- $maps | toJson -}}
{{- end -}}
