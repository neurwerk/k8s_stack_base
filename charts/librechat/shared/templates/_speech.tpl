{{/* Keep this validator identical in app/shared: each chart is independently packaged. */}}
{{- define "librechat.speechEndpoints" -}}
{{- $endpoints := dict -}}
{{- range $direction := list "stt" "tts" -}}
  {{- $settings := index $.Values.frontendLibrechat.speech $direction -}}
  {{- $field := printf "frontendLibrechat.speech.%s" $direction -}}
  {{- if not (and (kindIs "bool" $settings.enabled) (kindIs "bool" $settings.auth.enabled)) -}}
    {{- fail (printf "%s enabled and auth.enabled must be booleans" $field) -}}
  {{- end -}}
  {{- if and (eq $direction "tts") (not (kindIs "bool" $settings.allowExternal)) -}}
    {{- fail (printf "%s.allowExternal must be a boolean" $field) -}}
  {{- end -}}
  {{- if $settings.enabled -}}
    {{- if ne $settings.provider "openai-compatible" -}}
      {{- fail (printf "%s.provider must be openai-compatible" $field) -}}
    {{- end -}}
    {{- if not (and (kindIs "string" $settings.model) (not (empty (trim $settings.model))) (not (contains "${" $settings.model))) -}}
      {{- fail (printf "%s.model must be a nonempty literal string without environment substitutions" $field) -}}
    {{- end -}}
    {{- if eq $direction "tts" -}}
      {{- if not (and (kindIs "slice" $settings.voices) (not (empty $settings.voices))) -}}
        {{- fail (printf "%s.voices must be a nonempty list" $field) -}}
      {{- end -}}
      {{- range $settings.voices -}}
        {{- if not (and (kindIs "string" .) (not (empty (trim .))) (not (contains "${" .)) (ne (upper (trim .)) "ALL")) -}}
          {{- fail (printf "%s.voices entries must be nonempty literal names without environment substitutions; ALL is reserved (case-insensitive)" $field) -}}
        {{- end -}}
      {{- end -}}
    {{- end -}}
    {{- $url := $settings.url -}}
    {{- $octet := "(0|[1-9][0-9]?|1[0-9]{2}|2[0-4][0-9]|25[0-5])" -}}
    {{- $ipv4 := printf "%s\\.%s\\.%s\\.%s" $octet $octet $octet $octet -}}
    {{- $local := regexMatch (printf "^https?://%s:[1-9][0-9]{0,4}/[A-Za-z0-9._~/-]+$" $ipv4) $url -}}
    {{- $external := and (eq $direction "tts") $settings.allowExternal (regexMatch "^https://([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}(:443)?/[A-Za-z0-9._~/-]+$" $url) -}}
    {{- if not (or $local $external) -}}
      {{- fail (printf "%s.url must be a literal RFC1918 HTTP(S) URL with explicit port and full API path; only approved external TTS may use a DNS HTTPS:443 URL (no credentials, query, fragment or substitutions)" $field) -}}
    {{- end -}}
    {{- $parsed := urlParse $url -}}
    {{- $parts := splitList ":" $parsed.host -}}
    {{- $host := first $parts -}}
    {{- $port := 443 -}}
    {{- if $local -}}
      {{- $port = int (last $parts) -}}
      {{- if or (gt $port 65535) (not (regexMatch "^(10\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.|192\\.168\\.)" $host)) -}}
        {{- fail (printf "%s.url requires RFC1918 IPv4 and port 1-65535" $field) -}}
      {{- end -}}
    {{- end -}}
    {{- $_ := set $endpoints $direction (dict "host" $host "port" $port "local" $local "settings" $settings) -}}
  {{- end -}}
{{- end -}}
{{- toYaml $endpoints -}}
{{- end -}}
