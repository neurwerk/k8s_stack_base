{{/* Deployment validation only. The Engine owns profile recipes and language compatibility. */}}
{{- define "monitor-pii-engine.validateNer" -}}
{{- $values := .Values.monitorPiiEngine -}}
{{- $ner := $values.ner -}}
{{- if ne (kindOf $ner) "invalid" -}}
{{- $version := regexFind "[0-9]+\\.[0-9]+\\.[0-9]+" $values.image -}}
{{- if or (empty $version) (not (semverCompare ">=0.13.0" (default "0.0.0" $version))) -}}
{{- fail "canonical NER requires a compatible PII Engine image >= 0.13.0; the default published pin is not yet compatible" -}}
{{- end -}}
{{- if or (ne $values.device "cpu") $values.accelerator.enabled -}}
{{- fail "canonical NER requires the CPU Engine without a local GPU allocation" -}}
{{- end -}}
{{- if or (ne $values.analyzerBackend "local") (not (empty $values.remote.models)) -}}
{{- fail "canonical NER conflicts with legacy analyzerBackend or remote.models selectors" -}}
{{- end -}}
{{- $legacyNer := dict "strategy" "multilingual" "generalModel" "multilingual-pii" "perLanguage" (dict "en" "english-pii" "de" "german-pii" "nl" "dutch-pii") -}}
{{- if and $values.policy.pii.ner (not (deepEqual $values.policy.pii.ner $legacyNer)) -}}
{{- fail "canonical NER conflicts with nondefault policy.pii.ner" -}}
{{- end -}}
{{- $models := default dict $ner.models -}}
{{- $languages := default dict $ner.languageModels -}}
{{- if eq $ner.mode "disabled" -}}
{{- if or (not (empty $models)) (not (empty $languages)) -}}
{{- fail "disabled NER cannot select models or languageModels" -}}
{{- end -}}
{{- else -}}
{{- if or (empty $models) (empty $languages) -}}
{{- fail "active NER requires models and languageModels" -}}
{{- end -}}
{{- range $language, $id := $languages -}}
{{- if not (hasKey $models $id) -}}{{- fail "NER languageModels references an unknown model" -}}{{- end -}}
{{- end -}}
{{- range $id, $model := $models -}}
{{- if not (has $id (values $languages)) -}}{{- fail "NER models must all be referenced by languageModels" -}}{{- end -}}
{{- if eq $ner.mode "local" -}}
{{- range $field := list "endpoint" "modelName" "inferenceThreshold" "allowPrivateHttp" "tokenizerPath" "apiKeySecretRef" -}}
{{- if hasKey $model $field -}}{{- fail "local NER cannot configure remote model fields" -}}{{- end -}}
{{- end -}}
{{- else -}}
{{- if has $model.profile (list "spacy-en-sm-v1" "spacy-de-sm-v1" "spacy-nl-sm-v1") -}}{{- fail "remote NER cannot select local spaCy profiles" -}}{{- end -}}
{{- if empty $model.endpoint -}}{{- fail "remote NER requires an endpoint for every model" -}}{{- end -}}
{{- if and (hasPrefix "http://" $model.endpoint) (not $model.allowPrivateHttp) -}}
{{- fail "remote NER HTTP requires explicit allowPrivateHttp approval" -}}
{{- end -}}
{{- if eq $model.profile "gliner-multilingual-pii-v1" -}}
{{- if not (hasKey $model "inferenceThreshold") -}}{{- fail "GLiNER requires inferenceThreshold" -}}{{- end -}}
{{- end -}}
{{/* Explicit tokenizer resources determine wiring even for Engine-added profiles. */}}
{{- if or (not (empty $model.tokenizerPath)) (has $model.profile (list "kserve-en-openpii-v1" "kserve-de-superclinical-v1")) -}}
{{- if or (empty $model.modelName) (empty $model.tokenizerPath) (empty $ner.tokenizerClaimName) -}}
{{- fail "NER tokenizer resources require modelName, tokenizerPath and an existing tokenizer PVC (tokenizerClaimName)" -}}
{{- end -}}
{{- if or (contains "/../" $model.tokenizerPath) (hasSuffix "/.." $model.tokenizerPath) -}}
{{- fail "NER tokenizerPath must stay under /remote-tokenizers" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if eq $ner.mode "remote" -}}
{{- if empty $ner.egress -}}{{- fail "remote NER requires narrowly scoped destination egress" -}}{{- end -}}
{{- if and (not (empty $ner.tokenizerClaimName)) (empty (include "monitor-pii-engine.nerNeedsTokenizers" .)) -}}
{{- fail "NER tokenizerClaimName requires a model with tokenizerPath" -}}
{{- end -}}
{{- range $ner.egress -}}
{{- if hasSuffix "/128" .cidr -}}
{{- $address := trimSuffix "/128" .cidr -}}
{{- $groups := splitList ":" $address -}}
{{- $compressed := contains "::" $address -}}
{{- $groupCount := len (without $groups "") -}}
{{- if or (contains ":::" $address) (gt (len (regexFindAll "::" $address -1)) 1) (and (not $compressed) (ne $groupCount 8)) (and $compressed (ge $groupCount 8)) (and (hasPrefix ":" $address) (not (hasPrefix "::" $address))) (and (hasSuffix ":" $address) (not (hasSuffix "::" $address))) -}}
{{- fail "remote NER egress requires a valid scoped IPv6 host CIDR" -}}
{{- end -}}
{{- range $groups -}}
{{- if and (not (empty .)) (not (regexMatch "^[0-9a-fA-F]{1,4}$" .)) -}}
{{- fail "remote NER egress requires a valid scoped IPv6 host CIDR" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- else -}}
{{- if or (hasKey $ner "capacity") (not (empty $ner.egress)) (not (empty $ner.tokenizerClaimName)) -}}
{{- fail "NER capacity, egress and tokenizerClaimName are remote only" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "monitor-pii-engine.nerNeedsTokenizers" -}}
{{- range (default dict .Values.monitorPiiEngine.ner).models -}}
{{- if not (empty .tokenizerPath) -}}true{{- end -}}
{{- end -}}
{{- end -}}
