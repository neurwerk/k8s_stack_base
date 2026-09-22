"""Behavioral tests for Fluent Bit's application-error classifier."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLASSIFIER = ROOT / "charts/fluent-bit/files/application-errors.lua"


class FluentBitApplicationErrorTests(unittest.TestCase):
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
