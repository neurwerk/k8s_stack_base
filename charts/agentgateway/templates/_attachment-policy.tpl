{{/* One validated producer for versioned metadata and its size checks. Locality follows
the concrete routing branch, never names, groups, URLs or policy route classes. */}}
{{- define "infra-agentgateway.attachmentPolicy" -}}
{{- $engine := .Values.guardrails.llmPolicyEngine -}}
{{- $version := toJson $engine.attachmentPolicyVersion -}}
{{- if not (has $version (list "1" "2" "3")) -}}
{{- fail "guardrails.llmPolicyEngine.attachmentPolicyVersion must be integer 1, 2, or 3" -}}
{{- end -}}
{{- $maps := dict "image_forwarding" (dict) "face_protection" (dict) "local_models" (dict) -}}
{{- $models := include "infra-agentgateway.effectiveModels" . | fromYamlArray -}}
{{- $modelsByName := dict -}}
{{- $backend := .Values.infraAgentgatewayWrapper.llamacpp -}}
{{- $fallback := $engine.localTarget | default dict -}}
{{- if hasKey $fallback "supportsImages" -}}
{{- if ne $version "3" -}}
{{- fail "guardrails.llmPolicyEngine.localTarget.supportsImages requires attachmentPolicyVersion: 3 and compatible deployed services" -}}
{{- end -}}
{{- if not (kindIs "bool" $fallback.supportsImages) -}}
{{- fail "guardrails.llmPolicyEngine.localTarget.supportsImages must be a boolean" -}}
{{- end -}}
{{- end -}}
{{- if eq $version "3" -}}
{{- $_ := set $maps "image_models" (dict) -}}
{{- $_ := set $maps "image_reroutes" (dict) -}}
{{- end -}}
{{- range $model := $models -}}
{{- $name := $model.name -}}
{{- $_ := set $modelsByName $name $model -}}
{{- if and (hasKey $model "supportsImages") (ne $version "3") -}}
{{- fail (printf "model %q supportsImages requires attachmentPolicyVersion: 3 and compatible deployed services" $name) -}}
{{- end -}}
{{- if and (eq $version "1") (or (hasKey $model "imageForwarding") (hasKey $model "faceProtectionEnabled") (eq (toString $model.attachmentMode) "process")) -}}
{{- fail (printf "model %q new attachment settings require attachmentPolicyVersion: 2 and a deployed v2 consumer" $name) -}}
{{- end -}}
{{- if ne $version "1" -}}
{{- range $field := list "local" "piiReroute" "faceProtectionEnabled" "supportsImages" -}}
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
{{- $local := and (eq (toJson $model.local) "true") (not $model.piiReroute) (eq (toJson $backend.enabled) "true") (kindIs "string" $backend.host) (not (empty $backend.host)) (kindIs "string" $model.model) (not (empty $model.model)) -}}
{{- $images := and $local (eq (toJson $model.supportsImages) "true") -}}
{{- if and (eq $mode "passthrough") (or $pii $face (hasKey $model "imageForwarding")) -}}
{{- fail (printf "model %q passthrough requires piiEnabled:false, faceProtectionEnabled:false and no explicit imageForwarding" $name) -}}
{{- end -}}
{{- if and (ne $forward "none") (not $processing) -}}
{{- fail (printf "model %q image forwarding requires attachmentMode process or extract" $name) -}}
{{- end -}}
{{- if and (eq $version "2") (ne $forward "none") (not (and $.Values.docling.enabled (has $.Values.docling.inference.mode (list "private-vlm" "remote")))) -}}
{{- fail (printf "model %q image forwarding requires enabled Docling private-vlm or remote mode" $name) -}}
{{- end -}}
{{- if and (eq $version "3") (ne $forward "none") (not (and $.Values.docling.enabled (has $.Values.docling.inference.mode (list "internal-standard" "cpu" "private-vlm" "remote")))) -}}
{{- fail (printf "model %q image forwarding requires enabled Docling internal-standard/cpu or private-vlm/remote mode" $name) -}}
{{- end -}}
{{- if eq $forward "if-no-pii-detected" -}}
{{- if not (and $pii $face) -}}
{{- fail (printf "model %q if-no-pii-detected requires PII and face protection" $name) -}}
{{- end -}}
{{- end -}}
{{- if and (eq $forward "pii-unchecked") (or $face (not $local)) -}}
{{- fail (printf "model %q pii-unchecked requires faceProtectionEnabled:false and a concrete local:true target without piiReroute" $name) -}}
{{- end -}}
{{- if and (eq $version "3") (eq $forward "pii-unchecked") (not $images) -}}
{{- fail (printf "model %q v3 pii-unchecked requires supportsImages:true on its concrete local target" $name) -}}
{{- end -}}
{{- if ne $mode "passthrough" -}}
{{- $_ := set $maps.image_forwarding $name $forward -}}
{{- end -}}
{{- $_ := set $maps.face_protection $name $face -}}
{{- $_ := set $maps.local_models $name $local -}}
{{- if eq $version "3" -}}
{{- $_ := set $maps.image_models $name $images -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if eq $version "3" -}}
{{- $policy := (.Values.monitorPiiEngine | default dict).policy | default dict -}}
{{- $faces := ($policy.attachments | default dict).faces | default dict -}}
{{- $routing := $policy.routing | default dict -}}
{{- $action := "block" -}}
{{- if hasKey $faces "action" }}{{- $action = $faces.action }}{{- end -}}
{{- if not (has $action (list "block" "text-only" "reroute")) -}}
{{- fail "monitorPiiEngine.policy.attachments.faces.action must be block, text-only, or reroute" -}}
{{- end -}}
{{- if and (hasKey $faces "routeClass") (ne $action "reroute") -}}
{{- fail "monitorPiiEngine.policy.attachments.faces.routeClass is only allowed with action reroute" -}}
{{- end -}}
{{- if eq $action "reroute" -}}
{{- $routeClass := $routing.defaultTarget -}}
{{- if hasKey $faces "routeClass" }}{{- $routeClass = $faces.routeClass }}{{- end -}}
{{- if not (and (kindIs "string" $routeClass) (regexMatch "^[A-Za-z0-9_./:-]{1,128}$" $routeClass)) -}}
{{- fail "face reroute requires a safe 1-128 character routeClass or routing.defaultTarget" -}}
{{- end -}}
{{- $fallbackImages := and (eq (toJson $fallback.supportsImages) "true") (eq (toJson $backend.enabled) "true") (kindIs "string" $backend.host) (not (empty $backend.host)) (kindIs "string" $fallback.model) (not (empty $fallback.model)) -}}
{{- range $model := $models -}}
{{- $name := $model.name -}}
{{- if and (has ($model.attachmentMode | default "block") (list "process" "extract")) (ne (get $maps.image_forwarding $name) "none") -}}
{{- $destination := "" -}}
{{- if get $maps.local_models $name -}}
{{- if get $maps.image_models $name }}{{- $destination = $name }}{{- end -}}
{{- else if and $model.piiReroute (not $model.local) -}}
{{/* Mirror agentgateway-models.yaml with remote_allowed=false. The first local
match wins even if incapable; never approve a later target or fallback instead. */}}
{{- $matched := false -}}
{{- range $target := $routing.targets | default list -}}
{{- $row := get $modelsByName $target.name -}}
{{- $match := eq $routeClass $target.name -}}
{{- if $target.classPrefix }}{{- $match = hasPrefix $target.classPrefix $routeClass }}{{- end -}}
{{- if and (not $matched) $match (eq (toJson $row.local) "true") -}}
{{- $matched = true -}}
{{- if get $maps.image_models $target.name }}{{- $destination = $target.name }}{{- end -}}
{{- end -}}
{{- end -}}
{{- if and (not $matched) $fallbackImages -}}
{{- $destination = printf "%s-local" (include "infra-agentgateway.modelResourceName" $name) -}}
{{- end -}}
{{- end -}}
{{- if $destination }}{{- $_ := set $maps.image_reroutes $name (dict $routeClass $destination) }}{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- range $field, $map := $maps -}}
{{- if gt (len (toJson $map)) 16384 -}}
{{- fail (printf "%s destination metadata JSON exceeds the 16384-byte AgentGateway limit" $field) -}}
{{- end -}}
{{- end -}}
{{- $maps | toJson -}}
{{- end -}}
