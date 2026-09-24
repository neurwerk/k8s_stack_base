-- Only fixed application and failure categories leave the log pipeline as labels.
local applications = {
    ["frontend-librechat"] = "librechat",
    ["infra-agentgateway"] = "agentgateway",
    ["monitor-agentgateway-extproc"] = "agentgateway-extproc",
    ["monitor-pii-engine"] = "pii-engine",
    ["docling"] = "docling",
    ["auth-keycloak-api-key-bridge"] = "api-key-bridge",
    ["auth-keycloak"] = "keycloak",
    ["infra-postgres-auth"] = "postgres-auth",
    ["infra-postgres-operations"] = "postgres-operations",
}
local last_seen = {}

local function text(value)
    if type(value) ~= "string" then
        return ""
    end
    return value:sub(1, 4096):gsub("\27%[[%d;]*m", ""):lower()
end

local function contains(value, fragment)
    return value:find(fragment, 1, true) ~= nil
end

local levels = {
    trace = "TRACE", debug = "DEBUG", info = "INFO", information = "INFO",
    warn = "WARNING", warning = "WARNING", err = "ERROR", error = "ERROR",
    fatal = "FATAL", panic = "FATAL", critical = "FATAL",
}

local function log_level(record)
    for _, field in ipairs({"level", "levelname", "severity"}) do
        local value = text(record[field]):match("^%s*(%a+)%s*$")
        if levels[value] then
            return levels[value]
        end
    end
    local prefix = text(record.message or record.msg or record.log):gsub("^%s+", "")
    prefix = prefix:gsub("^%d%d%d%d%-%d%d%-%d%d[t ]%d%d:%d%d:%d%d[^%s]*%s+", "")
    prefix = prefix:gsub("^utc%s+", ""):gsub("^%[%d+%]%s*", "")
    local value = prefix:match("^%[(%a+)%]") or prefix:match("^(%a+)[%s:]")
    return levels[value] or "UNKNOWN"
end

local function classify(record, application)
    local message = text(record.message or record.msg or record.log)
    local prefix = message:gsub("^%d%d%d%d%-%d%d%-%d%d[t ]%d%d:%d%d:%d%d[^%s]*%s+", "")
    prefix = prefix:gsub("^%[%d+%]%s*", "")
    local level = text(record.level or record.levelname or record.severity)
    if level == "" then
        level = prefix:match("^(%a+)[%s:]") or ""
    end
    local error_text = text(record.error)
    if type(record.error) == "table" then
        error_text = text(record.error.type) .. " " .. text(record.error.code) .. " " .. text(record.error.message)
    end
    local diagnostic = message .. " " .. error_text
    local status_value = record["http.status"] or record.status_code or record.statusCode or record.status
    local status = nil
    if type(status_value) == "number" or type(status_value) == "string" then
        status = tonumber(status_value)
    end
    status = status or tonumber(message:match("http%.status=(%d%d%d)%f[%D]"))
        or tonumber(message:match('http/%d[%.%d]*"%s+(%d%d%d)%f[%D]'))
    local failure = level == "error" or level == "fatal" or level == "panic"
    local warning = level == "warn" or level == "warning"
    local transport_error = message:match('%serror="([^"]*)"') or message:match('%serror=([^%s]+)')
    local has_error = error_text ~= "" or (transport_error ~= nil and transport_error ~= ""
        and transport_error ~= '""' and transport_error ~= "null" and transport_error ~= "none")

    -- DocumentDB deliberately rejects this Atlas-only capability probe with
    -- CommandNotFound so compatible clients can continue against a non-Atlas server.
    if application == "postgres-operations"
        and contains(diagnostic, "user request failed")
        and contains(diagnostic, "command 'atlasversion' not found")
        and contains(diagnostic, "error_code=59") then
        return nil
    end

    -- These are caller outcomes, not application failures. The short memory wait
    -- merely lets chat proceed while the background task continues.
    if (status and status >= 400 and status < 500 and status ~= 408)
        or contains(diagnostic, "request blocked by policy")
        or contains(diagnostic, "request blocked by data policy")
        or contains(diagnostic, "reason=engine_invalid_request")
        or contains(diagnostic, "reason=engine_request_too_large")
        or contains(diagnostic, "[agentclient] memory processing timed out") then
        return nil
    end

    if contains(diagnostic, "client_aborted") or contains(diagnostic, "client disconnected")
        or contains(diagnostic, "client canceled") or contains(diagnostic, "client cancelled")
        or contains(diagnostic, "cancellederror") or contains(diagnostic, "aborterror") then
        return nil
    end
    if status == 408 or status == 504 then
        return "timeout"
    end
    if failure or warning or has_error then
        if contains(diagnostic, "timed out") or contains(diagnostic, "deadline exceeded")
            or contains(diagnostic, "deadline_exceeded") or contains(diagnostic, "timeout")
            or contains(diagnostic, "etimedout") then
            return "timeout"
        end
    end
    if status and status >= 500 and status < 600 then
        return "upstream_error"
    end
    if not (failure or warning or has_error) then
        return nil
    end
    if contains(diagnostic, "econnrefused") or contains(diagnostic, "econnreset")
        or contains(diagnostic, "connection refused") or contains(diagnostic, "connection reset")
        or contains(diagnostic, "connecterror") or contains(diagnostic, "engine_unavailable") then
        return "connection_error"
    end
    if contains(diagnostic, "ext_proc processing failed closed") then
        return "processing_error"
    end
    if contains(diagnostic, "failed to process memory") then
        return "background_error"
    end
    if failure or has_error then
        if contains(diagnostic, "upload") or contains(diagnostic, "s3") then
            return "upload_error"
        end
        if contains(diagnostic, "conversion") or contains(diagnostic, "convert") then
            return "document_error"
        end
        if contains(diagnostic, "stream") then
            return "stream_error"
        end
        return "application_error"
    end
    return nil
end

function application_error(tag, timestamp, record)
    -- Kubernetes metadata stays separate from application-supplied JSON fields.
    local payload = type(record.application) == "table" and record.application or record
    -- Reserved collector metadata is never accepted from application JSON.
    record.stack_log = {level = log_level(payload)}
    -- Never accept metric fields supplied in application JSON.
    record._stack_error_namespace = nil
    record._stack_error_application = nil
    record._stack_error_kind = nil
    record._stack_error_timestamp = nil
    local metadata = record.kubernetes
    if type(metadata) ~= "table" then
        return 2, timestamp, record
    end
    local namespace = metadata.namespace_name
    local application = applications[namespace]
    if namespace == "infra-rook-ceph" and text(metadata.pod_name):match("^rook%-ceph%-rgw%-") then
        application = "object-storage"
    elseif namespace == "kube-system" and metadata.container_name == "traefik" then
        application = "traefik"
    end
    if not application then
        return 2, timestamp, record
    end
    local kind = classify(payload, application)
    if not kind then
        return 2, timestamp, record
    end
    local seconds = type(timestamp) == "table" and timestamp.sec or timestamp
    if type(seconds) ~= "number" or seconds <= 0 or seconds > os.time() + 60 then
        return 2, timestamp, record
    end
    local key = application .. ":" .. kind
    last_seen[key] = math.max(last_seen[key] or 0, seconds)
    record._stack_error_namespace = namespace
    record._stack_error_application = application
    record._stack_error_kind = kind
    record.stack_log.failure_type = kind
    record._stack_error_timestamp = last_seen[key]
    return 2, timestamp, record
end

-- All applications share daily indices. Never dynamically map their JSON keys:
-- e.g. ts may be epoch seconds or an ISO timestamp, time may be a duration, and
-- error may be text or an object. Keep the complete original text searchable in
-- log; structured fields are used above for classification, not indexed as fields.
function opensearch_record(tag, timestamp, record)
    local result = {log = type(record.log) == "string" and record.log or ""}
    if type(record.stream) == "string" then
        result.stream = record.stream
    end
    local metadata = record.kubernetes
    if type(metadata) == "table" then
        local safe = {}
        for _, key in ipairs({"namespace_name", "pod_name", "container_name",
            "pod_id", "pod_ip", "host", "docker_id", "container_image", "container_hash"}) do
            if type(metadata[key]) == "string" then
                safe[key] = metadata[key]
            end
        end
        if next(safe) ~= nil then
            result.kubernetes = safe
        end
    end
    if type(record.stack_log) == "table" then
        local safe = {}
        for _, key in ipairs({"level", "failure_type"}) do
            if type(record.stack_log[key]) == "string" then
                safe[key] = record.stack_log[key]
            end
        end
        if next(safe) ~= nil then
            result.stack_log = safe
        end
    end
    return 2, timestamp, result
end
