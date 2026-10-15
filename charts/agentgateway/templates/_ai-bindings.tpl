{{- define "infra-agentgateway.aiLabels" -}}
app.kubernetes.io/name: ai-gateway
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: infra-agentgateway
{{- end -}}

{{/* Presence, not validity or provider response, freezes personal selection. */}}
{{- define "infra-agentgateway.aiCredentialSource" -}}
{{- if .local -}}
"none"
{{- else -}}
{{- $missing := ternary "company" "caller_supplied" (has "company" .credentialSources) -}}
{{- printf `(has(jwt.sub) && !("x-neurwerk-authorization" in request.headers)) ? %q : (%q in request.headers ? "caller_supplied" : %q)` $missing .credentialHeader $missing -}}
{{- end -}}
{{- end -}}

{{/* Only approved credential locations participate; never discard a wrong
provider header and accidentally choose a company account instead. */}}
{{- define "infra-agentgateway.aiCredentialAuthorization" -}}
{{- if .local -}}
{{- `!["authorization", "x-api-key", "x-goog-api-key"].exists(h, h in request.headers && !(h == "authorization" && has(jwt.sub) && !("x-neurwerk-authorization" in request.headers)))` -}}
{{- else -}}
{{- $company := "true" -}}
{{- if has "company" .credentialSources }}{{ $company = printf `%q in (has(jwt.sub) ? jwt.resource_access.agentgateway.roles : extauthz.permissions)` (printf "provider:%s:company-use" .providerId) }}{{ end -}}
{{- printf `(["authorization", "x-api-key", "x-goog-api-key"].all(h, h == %q || !(h in request.headers) || (h == "authorization" && has(jwt.sub) && !("x-neurwerk-authorization" in request.headers)))) && ((has(jwt.sub) && !("x-neurwerk-authorization" in request.headers)) ? (%t && (%s) && !["x-api-key", "x-goog-api-key"].exists(h, h in request.headers)) : (%q in request.headers ? %t : (%s)))` .credentialHeader (has "company" .credentialSources) $company .credentialHeader (has "caller_supplied" .credentialSources) $company -}}
{{- end -}}
{{- end -}}

{{/* Validated effective bindings feed native routing, v5 admission and Studio. */}}
{{- define "infra-agentgateway.aiBindings" -}}
{{- $cfg := .Values.aiGateway -}}
{{- $effective := list -}}
{{- if ne $cfg.enabled true }}{{ fail "aiGateway.enabled must be true; the unbound gateway has been removed" }}{{ end -}}
{{- if $cfg.enabled -}}
{{- range $key, $_ := $cfg -}}
{{- if not (has $key (list "enabled" "apiPort" "bindings" "proxy")) }}{{ fail (printf "unknown aiGateway field %s" $key) }}{{ end -}}
{{- end -}}

{{- if and $cfg.bindings (not $.Values.guardrails.llmPolicyEngine.enabled) }}{{ fail "model bindings require guardrails.llmPolicyEngine.enabled" }}{{ end -}}
{{- if not $.Values.agentgateway.agentgatewayModels.enabled }}{{ fail "aiGateway requires native AgentgatewayModel support" }}{{ end -}}
{{- $ports := list (int $.Values.infraAgentgatewayWrapper.ports.service.http) 443 15000 15020 15021 -}}
{{- range $port := list $cfg.apiPort $cfg.proxy.port -}}
{{- if or (not (regexMatch `^[1-9][0-9]*$` (toJson $port))) (gt (int $port) 65535) (has (int $port) $ports) }}{{ fail "aiGateway ports must be distinct, nonreserved TCP ports" }}{{ end -}}
{{- $ports = append $ports (int $port) -}}
{{- end -}}
{{- if or (not (kindIs "slice" $cfg.bindings)) (gt (len $cfg.bindings) 8) }}{{ fail "aiGateway.bindings must be a list of at most 8 bindings" }}{{ end -}}
{{- $ids := dict -}}{{- $hosts := dict -}}{{- $destinations := dict -}}
{{- $roles := include "infra-agentgateway.effectiveRoles" . | fromYamlArray -}}
{{- $profiles := .Files.Get "catalog/ai-profiles.yaml" | fromYaml -}}
{{- $upstreams := dict -}}
{{- $catalog := dict -}}
{{- range include "infra-agentgateway.effectiveModels" . | fromYamlArray }}{{ $_ := set $catalog .name . }}{{ end -}}
{{- range $entry := $cfg.bindings -}}
{{- if not (kindIs "map" $entry) }}{{ fail "aiGateway bindings must be maps" }}{{ end -}}
{{- range $key, $_ := $entry -}}
{{- if not (has $key (list "id" "enabled" "providerId" "provider" "upstreamURL" "local" "credentialSources" "authSecret" "api" "inspect" "profiles" "models" "catalogModels" "localReroute" "attachments")) }}{{ fail (printf "unsupported aiGateway binding field %s; use one provider/upstream binding with credentialSources" $key) }}{{ end -}}
{{- end -}}
{{- if not (kindIs "bool" $entry.enabled) }}{{ fail "aiGateway bindings require explicit boolean enabled" }}{{ end -}}
{{- $id := $entry.id | default "" -}}
{{- if or (not (regexMatch `^[a-z0-9]([-a-z0-9]{0,30}[a-z0-9])?$` $id)) (hasKey $ids $id) }}{{ fail "aiGateway binding IDs must be unique DNS labels of at most 32 characters" }}{{ end -}}
{{- $_ := set $ids $id true -}}
{{- if $entry.enabled -}}
{{- $binding := mergeOverwrite (dict "api" true "inspect" false "local" false "provider" "OpenAI" "providerId" nil) (deepCopy $entry) -}}
{{- range $flag := list "api" "inspect" "local" -}}
{{- if not (kindIs "bool" (get $binding $flag)) }}{{ fail "binding api/inspect/local must be booleans" }}{{ end -}}
{{- end -}}
{{- if not (or $binding.api $binding.inspect) }}{{ fail "a binding needs an API or inspection entry" }}{{ end -}}
{{- if and $binding.api (not $.Values.externalGateway.enabled) }}{{ fail "API bindings require externalGateway.enabled for their advertised HTTPS URLs" }}{{ end -}}
{{- if not (has $binding.provider (list "OpenAI" "Openrouter" "Deepseek" "Anthropic" "Gemini" "Custom")) }}{{ fail "binding provider has no admitted native path mapping" }}{{ end -}}
{{- if and (eq $binding.provider "Custom") (not $binding.local) }}{{ fail "Custom bindings are reserved for explicit credential-free local models" }}{{ end -}}
{{- if and (eq $binding.provider "Gemini") $binding.api }}{{ fail "Gemini API mounts require separate /bindings/<id>/v1beta acceptance; use api:false with inspected native Google paths" }}{{ end -}}
{{- $sources := $binding.credentialSources -}}
{{- if or (not (kindIs "slice" $sources)) (empty $sources) (gt (len $sources) 2) (ne (len $sources) (len (uniq $sources))) }}{{ fail "binding credentialSources must be a nonempty unique list" }}{{ end -}}
{{- range $source := $sources -}}
{{- if not (has $source (list "company" "caller_supplied" "none")) }}{{ fail "binding credentialSources accepts only company, caller_supplied or none" }}{{ end -}}
{{- end -}}
{{- if or (and $binding.local (ne (toJson $sources) `["none"]`)) (and (not $binding.local) (has "none" $sources)) }}{{ fail "credential-free bindings require an explicit local target with only credentialSources: [none]" }}{{ end -}}
{{- if and (not $binding.local) (empty $binding.providerId) }}{{ fail "credential-bearing bindings require providerId" }}{{ end -}}
{{- if and $binding.providerId (not (regexMatch `^[a-z0-9][a-z0-9-]{0,47}$` $binding.providerId)) }}{{ fail "binding providerId must be a lowercase provider identifier" }}{{ end -}}
{{- if has "company" $sources -}}
{{- if not (regexMatch `^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$` ($binding.authSecret | default "")) }}{{ fail "company bindings require an authSecret name" }}{{ end -}}
{{- if not (has (printf "provider:%s:company-use" $binding.providerId) $roles) }}{{ fail "company binding requires its provider company-use role" }}{{ end -}}
{{- else if hasKey $binding "authSecret" -}}{{ fail "authSecret requires company in credentialSources" }}{{ end -}}
{{- $url := $binding.upstreamURL | default "" -}}
{{- if not (regexMatch `^https?://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[1-9][0-9]{0,4})?(/[A-Za-z0-9._~/-]*)?$` $url) }}{{ fail "binding upstreamURL must be a fixed HTTP(S) URL without credentials, query or fragment" }}{{ end -}}
{{- $parsed := urlParse $url -}}{{- $host := first (splitList ":" $parsed.host) -}}
{{- if and (contains ":" $parsed.host) (gt (int (last (splitList ":" $parsed.host))) 65535) }}{{ fail "binding upstream port must be at most 65535" }}{{ end -}}
{{- if or (eq $host "localhost") (hasSuffix ".localhost" $host) (and (regexMatch `^[0-9.]+$` $host) (not (and $binding.local (regexMatch `^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)` $host)))) }}{{ fail "binding upstreamURL requires an approved DNS host or an explicit private local address" }}{{ end -}}
{{- if and (not $binding.local) (ne $parsed.scheme "https") }}{{ fail "provider credentials require verified upstream HTTPS" }}{{ end -}}
{{- $upstreamIdentity := toJson (list $binding.providerId $binding.provider (trimSuffix "/" $url)) -}}
{{- if hasKey $upstreams $upstreamIdentity }}{{ fail "one provider/upstream binding must own all its credential modes and models" }}{{ end -}}
{{- $_ := set $upstreams $upstreamIdentity true -}}
{{- if $binding.inspect -}}
{{- if not $cfg.proxy.enabled }}{{ fail "inspection bindings require aiGateway.proxy.enabled" }}{{ end -}}
{{- if or (ne $parsed.scheme "https") (not (has $parsed.host (list $host (printf "%s:443" $host)))) }}{{ fail "inspection requires an exact HTTPS upstream on port 443" }}{{ end -}}
{{- if hasKey $hosts $host }}{{ fail "only one inspected binding may own each exact hostname" }}{{ end -}}
{{- $_ := set $hosts $host true -}}
{{- end -}}
{{- if or (not (kindIs "slice" $binding.profiles)) (empty $binding.profiles) (gt (len $binding.profiles) 2) (ne (len $binding.profiles) (len (uniq $binding.profiles))) }}{{ fail "binding profiles must select one or two unique implemented profiles" }}{{ end -}}
{{- range $profile := $binding.profiles -}}
{{- if or (not (hasKey $profiles $profile)) (not (has $binding.provider ((get $profiles $profile).providers | default list))) }}{{ fail "binding profile/provider combination has no admitted native path mapping" }}{{ end -}}
{{- $_ := set $binding "credentialHeader" (get $profiles $profile).credentialHeader -}}
{{- end -}}
{{- $selectedModels := $binding.models | default list -}}
{{- range $id := $binding.catalogModels | default list -}}
{{- if not (hasKey $catalog $id) }}{{ fail (printf "binding selects unknown catalog model %s" $id) }}{{ end -}}
{{- $row := get $catalog $id -}}
{{- if not $binding.local -}}
{{- if or (ne $row.provider $binding.provider) (ne (trimSuffix "/" $row.baseURL) (trimSuffix "/" $binding.upstreamURL)) (ne ($row.authSecret | default "") ($binding.authSecret | default "")) }}{{ fail "catalog model provider, upstream and company credential must match its binding" }}{{ end -}}
{{- else if ne (toJson $row.local) "true" }}{{ fail "local bindings require explicitly local catalog models" }}{{ end -}}
{{- $model := dict "name" $id "upstreamModel" $row.model "displayName" ($row.label | default $id) "piiEnabled" ($row.piiEnabled | default true) "contentTracingEnabled" ($row.contentTracingEnabled | default false) "attachments" ($row.attachments | default ($binding.attachments | default dict)) -}}
{{- if hasKey $row "piiEnabled" }}{{ $_ := set $model "piiEnabled" $row.piiEnabled }}{{ end -}}
{{- if hasKey $row "supportsImages" }}{{ $_ := set $model "supportsImages" $row.supportsImages }}{{ end -}}
{{- $selectedModels = append $selectedModels $model -}}
{{- end -}}
{{- if or (not (kindIs "slice" $selectedModels)) (empty $selectedModels) (gt (len $selectedModels) 32) }}{{ fail "binding models must select 1-32 canonical models" }}{{ end -}}
{{- $models := list -}}{{- $aliases := dict -}}{{- $modelBindings := dict -}}
{{- range $model := $selectedModels -}}
{{- range $key, $_ := $model -}}
{{- if not (has $key (list "name" "upstreamModel" "displayName" "piiEnabled" "contentTracingEnabled" "attachments" "supportsImages")) }}{{ fail (printf "unsupported binding model field %s" $key) }}{{ end -}}
{{- end -}}
{{- $name := $model.name | default "" -}}{{- $upstream := $model.upstreamModel | default "" -}}
{{- if not (regexMatch `^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$` $name) }}{{ fail "binding model name must be a canonical permission-safe ID" }}{{ end -}}
{{- if not (regexMatch `^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$` $upstream) }}{{ fail "binding upstreamModel must be an exact provider model ID" }}{{ end -}}
{{- if hasKey $modelBindings $name }}{{ fail "duplicate canonical model in binding" }}{{ end -}}
{{- range $alias := list $name $upstream | uniq -}}
{{- if hasKey $aliases $alias }}{{ fail "canonical and upstream model aliases must not collide" }}{{ end -}}
{{- $_ := set $aliases $alias true -}}
{{- end -}}
{{- if hasKey $destinations $name }}{{ fail "each canonical model must belong to exactly one provider/upstream binding" }}{{ end -}}
{{- $_ := set $destinations $name true -}}
{{- $normalized := mergeOverwrite (dict "piiEnabled" true "contentTracingEnabled" false "attachments" ($binding.attachments | default dict) "displayName" $name) (deepCopy $model) -}}
{{- if or (not (kindIs "string" $normalized.displayName)) (empty $normalized.displayName) (gt (len $normalized.displayName) 128) }}{{ fail "binding model displayName must be a nonempty string of at most 128 characters" }}{{ end -}}
{{- range $flag := list "piiEnabled" "contentTracingEnabled" -}}
{{- if not (kindIs "bool" (get $normalized $flag)) }}{{ fail "binding model PII/tracing flags must be booleans" }}{{ end -}}
{{- end -}}
{{- $permissions := list "llm:invoke" (printf "model:%s:invoke" $name) -}}
{{- range $permission := $permissions -}}
{{- if not (has $permission $roles) }}{{ fail (printf "binding requires declared AgentGateway role %s" $permission) }}{{ end -}}
{{- end -}}
{{- $_ := set $normalized "model" $upstream -}}{{- $_ := set $normalized "local" $binding.local -}}
{{- $models = append $models $normalized -}}
{{- $_ := set $modelBindings $name (dict "upstream_model" $upstream "required_permissions" $permissions "content_tracing_enabled" $normalized.contentTracingEnabled) -}}
{{- end -}}
{{- $context := deepCopy $ -}}
{{- $_ := set $context.Values.openrouterCatalog "enabled" false -}}
{{- $_ := set $context.Values.guardrails.llmPolicyEngine "models" $models -}}
{{- $_ := set $context.Values.guardrails.llmPolicyEngine "attachmentPolicyVersion" "4.1" -}}
{{- $_ := set $context.Values.guardrails.llmPolicyEngine "localTarget" (dict) -}}
{{- $_ := set $context.Values.infraAgentgatewayWrapper "llamacpp" (dict "enabled" $binding.local "host" $host) -}}
{{- $attachment := include "infra-agentgateway.attachmentPolicy" $context | fromJson -}}
{{- $_ := unset $attachment "image_reroutes" -}}
{{- $_ := set $binding "models" $models -}}{{- $_ := set $binding "modelBindings" $modelBindings -}}
{{- $_ := set $binding "attachmentPolicy" $attachment -}}{{- $_ := set $binding "host" $host -}}
{{- $_ := set $binding "prefix" (printf "/bindings/%s" $id) -}}
{{- $operations := list -}}
{{- range $profileId := $binding.profiles -}}
{{- $profile := get $profiles $profileId -}}
{{- range $operation := $profile.operations -}}
{{- $paths := list -}}
{{- if eq $profileId "google-generate-content-v1" -}}
{{- range $alias := keys $aliases | sortAlpha -}}
{{- $paths = append $paths (printf "/v1beta/models/%s:%s" $alias $operation.id) -}}
{{- end -}}
{{- else -}}
{{- $paths = list $profile.path -}}
{{- end -}}
{{- $operations = append $operations (dict "profile" $profileId "id" $operation.id "paths" $paths) -}}
{{- end -}}
{{- end -}}
{{- $_ := set $binding "operations" $operations -}}
{{- if or (gt (len (toJson $modelBindings)) 16384) (gt (len (toJson $attachment)) 14000) }}{{ fail "v5 binding exceeds native metadata expression limits" }}{{ end -}}
{{- $effective = append $effective $binding -}}
{{- end -}}
{{- end -}}
{{- $localTargets := dict -}}
{{- range $binding := $effective -}}
{{- if $binding.local -}}
{{- range $model := $binding.models -}}
{{- $_ := set $localTargets $model.name (dict "binding_id" $binding.id "upstream_model" $model.upstreamModel "required_permissions" (get $binding.modelBindings $model.name).required_permissions "content_tracing_enabled" $model.contentTracingEnabled "upstreamURL" $binding.upstreamURL "provider" $binding.provider) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- range $binding := $effective -}}
{{- $targets := dict -}}
{{- if $binding.localReroute -}}
{{- if or $binding.local (not (has "company" $binding.credentialSources)) (not (hasKey $localTargets $binding.localReroute)) }}{{ fail "localReroute requires a company binding and an explicitly selected local target" }}{{ end -}}
{{- $target := deepCopy (get $localTargets $binding.localReroute) -}}
{{- $_ := set $binding "localRerouteBackend" (deepCopy $target) -}}
{{- $_ := unset $target "upstreamURL" -}}{{- $_ := unset $target "provider" -}}
{{- $_ := set $targets $binding.localReroute $target -}}
{{- range $name, $model := $binding.modelBindings }}{{ $_ := set $model "allowed_reroutes" (list $binding.localReroute) }}{{ end -}}
{{- end -}}
{{- $_ := set $binding "rerouteTargets" $targets -}}
{{- end -}}
{{- if not (kindIs "bool" $cfg.proxy.enabled) }}{{ fail "aiGateway.proxy.enabled must be boolean" }}{{ end -}}
{{- if $cfg.proxy.enabled -}}
{{- range $field, $_ := $cfg.proxy -}}
{{- if not (has $field (list "enabled" "hostname" "port" "publicPort" "tlsSecretName" "signerSecretName" "ingressPeers")) }}{{ fail (printf "unknown aiGateway.proxy field %s" $field) }}{{ end -}}
{{- end -}}
{{- range $field := list "hostname" "tlsSecretName" "signerSecretName" -}}
{{- if not (regexMatch `^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$` (get $cfg.proxy $field)) }}{{ fail (printf "aiGateway.proxy.%s must be an exact DNS name" $field) }}{{ end -}}
{{- end -}}
{{- if or (not (regexMatch `^[1-9][0-9]*$` (toJson $cfg.proxy.publicPort))) (gt (int $cfg.proxy.publicPort) 65535) }}{{ fail "aiGateway.proxy.publicPort must be a TCP port" }}{{ end -}}
{{- if eq $cfg.proxy.hostname $.Values.infraAgentgatewayWrapper.hostname }}{{ fail "API and proxy hostnames must differ for shared HTTPS ingress" }}{{ end -}}
{{- if or (eq $cfg.proxy.tlsSecretName "infra-agentgateway-tls") (eq $cfg.proxy.tlsSecretName $cfg.proxy.signerSecretName) }}{{ fail "proxy TLS must use its own Secret, separate from API TLS and inspection signing" }}{{ end -}}
{{- if empty $hosts }}{{ fail "proxy requires at least one inspected binding" }}{{ end -}}
{{- if or (not (kindIs "slice" $cfg.proxy.ingressPeers)) (empty $cfg.proxy.ingressPeers) }}{{ fail "proxy requires explicit ingressPeers for the selected L4 path" }}{{ end -}}
{{- range $peer := $cfg.proxy.ingressPeers -}}
{{- if or (empty (($peer.namespaceSelector | default dict).matchLabels)) (empty (($peer.podSelector | default dict).matchLabels)) (hasKey $peer "ipBlock") }}{{ fail "proxy ingressPeers require explicit namespace and pod labels" }}{{ end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $effective | toJson -}}
{{- end -}}

{{- define "infra-agentgateway.aiRevision" -}}
{{- dict "bindings" (include "infra-agentgateway.aiBindings" . | fromJsonArray) "profiles" (.Files.Get "catalog/ai-profiles.yaml" | fromYaml) "models" (include "infra-agentgateway.effectiveModels" . | fromYamlArray) "mcp" (include "infra-agentgateway.effectiveMcpServers" . | fromYamlArray) | toJson | sha256sum -}}
{{- end -}}
