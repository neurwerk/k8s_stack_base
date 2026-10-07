{{- define "librechat.costTokenConfig" -}}
{{- $root := .root -}}
{{- $costs := $root.Values.frontendLibrechat.costs -}}
{{- if not (kindIs "bool" $costs.enabled) }}{{ fail "frontendLibrechat.costs.enabled must be boolean" }}{{ end -}}
{{- $tokens := dict -}}
{{- if $costs.enabled -}}
{{- range $name, $model := .catalog.pricingModels -}}
{{- $context := index $costs.contextWindows $name -}}
{{- if not (regexMatch `^[1-9][0-9]*$` (toJson $context)) }}{{ fail (printf "frontendLibrechat.costs.contextWindows requires a positive integer for %q" $name) }}{{ end -}}
{{- $entry := dig $model.provider "models" $model.model (dict) $root.Values.providers -}}
{{- $rates := $entry.rates | default dict -}}
{{- $config := dict "context" $context -}}
{{- range $source, $target := dict "input" "prompt" "output" "completion" "cacheRead" "cacheRead" "cacheWrite" "cacheWrite" -}}
{{- if or (hasKey $rates $source) (has $source (list "input" "output")) -}}
{{- $rate := index $rates $source -}}
{{- if not (regexMatch `^[0-9]+(\.[0-9]+)?$` (toString $rate)) }}{{ fail (printf "LibreChat costs require a nonnegative %s rate for %q (%s/%s)" $source $name $model.provider $model.model) }}{{ end -}}
{{- $_ := set $config $target (float64 $rate) -}}
{{- end -}}
{{- end -}}
{{- $_ := set $tokens $name $config -}}
{{- end -}}
{{- end -}}
{{- $tokens | toYaml -}}
{{- end -}}
