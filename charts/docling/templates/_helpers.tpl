{{- define "docling.labels" -}}
app.kubernetes.io/name: docling
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: docling
{{- end }}

{{- define "docling.validate" -}}
{{- $d := .Values.docling -}}
{{- if not (kindIs "bool" $d.enabled) }}{{ fail "docling.enabled must be boolean" }}{{ end -}}
{{- if not (regexMatch `^https://[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?(:[1-9][0-9]{0,4})?/v1/chat/completions$` $d.inference.url) -}}
{{- fail "docling.inference.url must be a full HTTPS /v1/chat/completions URL without userinfo, query or fragment" -}}
{{- end -}}
{{- $url := urlParse $d.inference.url -}}
{{- if not (regexMatch `^[1-9][0-9]*$` (toJson $d.inference.port)) }}{{ fail "docling.inference.port must be an integer" }}{{ end -}}
{{- $port := "443" -}}
{{- if contains ":" $url.host }}{{ $port = last (splitList ":" $url.host) }}{{ end -}}
{{- if or (ne (int $port) (int $d.inference.port)) (gt (int $port) 65535) }}{{ fail "docling.inference.port must match the URL port" }}{{ end -}}
{{- if or (not (kindIs "string" $d.inference.model)) (not (regexMatch `^[^\s]+$` $d.inference.model)) }}{{ fail "docling.inference.model must be a nonempty alias without whitespace" }}{{ end -}}
{{- range $ref := list $d.apiKeySecretRef $d.inference.tokenSecretRef -}}
{{- if or (not (regexMatch `^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$` $ref.name)) (not (regexMatch `^[a-zA-Z0-9._-]+$` $ref.key)) }}{{ fail "docling requires existing API and inference Secret name/key references" }}{{ end -}}
{{- end -}}
{{- if eq (toJson $d.apiKeySecretRef) (toJson $d.inference.tokenSecretRef) }}{{ fail "docling API and inference credentials must be separate references" }}{{ end -}}
{{- if not $d.inference.cidrs }}{{ fail "docling.inference.cidrs requires private inference destinations" }}{{ end -}}
{{- range $cidr := $d.inference.cidrs -}}
{{- if not (regexMatch `^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$` $cidr) }}{{ fail "docling inference CIDRs must be RFC1918 IPv4" }}{{ end -}}
{{- $parts := splitList "/" $cidr -}}
{{- $octets := splitList "." (first $parts) -}}
{{- $prefix := int (last $parts) -}}
{{- range $octet := $octets }}{{ if or (gt (int $octet) 255) (ne (toString (int $octet)) $octet) }}{{ fail "invalid docling IPv4 CIDR" }}{{ end }}{{ end -}}
{{- $a := int (index $octets 0) }}{{ $b := int (index $octets 1) -}}
{{- if not (and (le $prefix 32) (or (and (eq $a 10) (ge $prefix 8)) (and (eq $a 172) (ge $b 16) (le $b 31) (ge $prefix 12)) (and (eq $a 192) (eq $b 168) (ge $prefix 16)))) }}{{ fail "docling inference CIDRs must stay within RFC1918 ranges" }}{{ end -}}
{{- end -}}
{{- range $name := list "fileBytes" "totalBytes" "count" "pages" -}}
{{- $value := index $.Values.documentAttachments $name -}}
{{- if not (regexMatch `^[1-9][0-9]*$` (toJson $value)) }}{{ fail (printf "documentAttachments.%s must be a positive integer" $name) }}{{ end -}}
{{- end -}}
{{- if lt (int64 .Values.documentAttachments.totalBytes) (int64 .Values.documentAttachments.fileBytes) }}{{ fail "documentAttachments.totalBytes must cover fileBytes" }}{{ end -}}
{{- range $value := list $d.documentTimeoutSeconds $d.syncWaitSeconds $d.inference.timeoutSeconds $d.resultRemovalDelaySeconds $d.cleanup.retentionSeconds $d.cleanup.activeDeadlineSeconds -}}
{{- if not (regexMatch `^[1-9][0-9]*$` (toJson $value)) }}{{ fail "docling time bounds must be positive integer seconds" }}{{ end -}}
{{- end -}}
{{- if or (gt (int $d.documentTimeoutSeconds) 3600) (gt (int $d.inference.timeoutSeconds) (int $d.documentTimeoutSeconds)) (le (int $d.syncWaitSeconds) (int $d.documentTimeoutSeconds)) (gt (int $d.syncWaitSeconds) 3660) (gt (int $d.resultRemovalDelaySeconds) 600) (gt (int $d.cleanup.retentionSeconds) 3600) (gt (int $d.cleanup.activeDeadlineSeconds) 300) }}{{ fail "docling time bounds are inconsistent or exceed safety maxima" }}{{ end -}}
{{- range $resources := list $d.resources $d.cleanup.resources -}}
{{- range $resource := list "cpu" "memory" "ephemeral-storage" -}}
{{- $amounts := dict -}}
{{- range $kind := list "requests" "limits" -}}
{{- $value := index $resources $kind $resource -}}
{{- $pattern := `^[1-9][0-9]*(Ki|Mi|Gi)?$` -}}
{{- if eq $resource "cpu" }}{{ $pattern = `^[1-9][0-9]*m?$` }}{{ end -}}
{{- if not (regexMatch $pattern (toString $value)) }}{{ fail "docling resources require positive cpu, memory and ephemeral-storage requests/limits" }}{{ end -}}
{{- $unit := regexFind `[A-Za-z]+$` (toString $value) -}}
{{- $factor := get (dict "" 1 "m" 0.001 "Ki" 1024 "Mi" 1048576 "Gi" 1073741824) $unit -}}
{{- $_ := set $amounts $kind (mulf (regexFind `^[0-9]+` (toString $value)) $factor) -}}
{{- end -}}
{{- if gt (float64 $amounts.requests) (float64 $amounts.limits) }}{{ fail "docling resource requests must not exceed limits" }}{{ end -}}
{{- end -}}
{{- range $kind, $entries := $resources }}{{ range $key, $_ := $entries }}{{ if not (has $key (list "cpu" "memory" "ephemeral-storage")) }}{{ fail "docling is CPU-only; unsupported resource" }}{{ end }}{{ end }}{{ end -}}
{{- end -}}
{{- range $size := list $d.scratchSizeLimit $d.tmpSizeLimit }}{{ if not (regexMatch `^[1-9][0-9]*(Mi|Gi)$` $size) }}{{ fail "docling disposable volume bounds must be positive Mi/Gi quantities" }}{{ end }}{{ end -}}
{{- end }}
