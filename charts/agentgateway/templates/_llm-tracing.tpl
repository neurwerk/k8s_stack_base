{{/*
Keep tool schemas in one JSON attribute: flattening loses empty lists and can
exhaust span attribute limits for large schemas. request.body is the snapshot
after HTTP policies (including extProc), before provider translation. Never use
response.body: it can contain restored content and does not assemble SSE calls.
*/}}
{{- define "infra-agentgateway.llmTraceInput" -}}
toJson({"messages": llm.prompt}.merge(default(
  [json(request.body)].map(body, {
    "tools": default(body.tools, []),
    "tool_choice": default(body.tool_choice, null),
    "parallel_tool_calls": default(body.parallel_tool_calls, null),
    "functions": default(body.functions, null),
    "function_call": default(body.function_call, null),
    "toolConfig": default(body.toolConfig, null)
  })[0], {})))
{{- end -}}

{{- define "infra-agentgateway.llmTraceOutput" -}}
(has(llm.requestModel) ? toJson({
  "messages": default(llm.completion, []).map(c, {"role": "assistant", "content": c}),
  "tool_calls": default(llm.toolCalls, null)
}) : null)
{{- end -}}
