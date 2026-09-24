"""Behavioral tests for Fluent Bit's application-error classifier."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLASSIFIER = ROOT / "charts/fluent-bit/files/application-errors.lua"


class FluentBitApplicationErrorTests(unittest.TestCase):
    def test_mixed_json_types_do_not_escape_into_shared_index(self) -> None:
        fixture = f"""
dofile({json.dumps(str(CLASSIFIER))})
local timestamp = {{sec = os.time(), nsec = 123}}
for _, payload in ipairs({{
    {{ts = 123.5, time = "2026-01-01T00:00:00Z", error = "connection refused"}},
    {{ts = "2026-01-01T00:00:00Z", time = "12ms", error = {{message = "connection refused"}}}},
}}) do
    payload.level = "error"
    payload.kubernetes = {{namespace_name = "untrusted"}}
    payload.stack_log = {{level = "INFO"}}
    local record = {{
        log = '{{"original":"retained for search"}}',
        stream = "stderr",
        application = payload,
        kubernetes = {{namespace_name = "frontend-librechat", pod_name = "test-pod"}},
    }}
    application_error("kube.test", timestamp, record)
    assert(record._stack_error_kind == "connection_error")
    assert(record._stack_error_namespace == "frontend-librechat")
    local code, output_time, output = opensearch_record("kube.test", timestamp, record)
    assert(code == 2 and output_time == timestamp)
    assert(output.log == record.log and output.stream == "stderr")
    assert(output.kubernetes.namespace_name == "frontend-librechat")
    assert(output.kubernetes.pod_name == "test-pod")
    assert(output.stack_log.level == "ERROR")
    assert(output.stack_log.failure_type == "connection_error")
    assert(output.application == nil and output.ts == nil and output.time == nil)
    assert(output._stack_error_kind == nil)
end
local plain = {{log = "unstructured log", kubernetes = "invalid", stream = {{}}}}
application_error("kube.test", timestamp, plain)
local _, _, output = opensearch_record("kube.test", timestamp, plain)
assert(output.log == "unstructured log")
assert(output.kubernetes == nil and output.stream == nil)
assert(output.stack_log.level == "UNKNOWN")
"""
        subprocess.run(
            ["lua", "-"], input=fixture, text=True, check=True, capture_output=True
        )

    def test_expected_documentdb_probe_is_the_only_scoped_exclusion(self) -> None:
        fixture = f"""
dofile({json.dumps(str(CLASSIFIER))})

local function classified(namespace, message)
    local record = {{
        log = message,
        kubernetes = {{ namespace_name = namespace }},
    }}
    application_error("kube.test", os.time(), record)
    return record._stack_error_kind
end

local atlas_probe = "ERROR gateway.request: User request failed. "
    .. "error_message_internal=Command 'atlasVersion' not found. error_code=59"

assert(classified("infra-postgres-operations", atlas_probe) == nil)
assert(classified(
    "infra-postgres-operations",
    "ERROR gateway.request: User request failed. "
        .. "error_message_internal=Command 'otherCommand' not found. error_code=59"
) == "application_error")
assert(classified("infra-postgres-auth", atlas_probe) == "application_error")
assert(classified(
    "infra-postgres-operations",
    "ERROR gateway runtime connection reset"
) == "connection_error")
"""

        subprocess.run(
            ["lua", "-"],
            input=fixture,
            text=True,
            check=True,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()
