"""Opt-in Helm -> protobuf -> real consumer validation, without network calls.

Pass an explicit consumer source tree and its installed Python environment.
No sibling-repository layout or consumer version is assumed by normal Base tests.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "charts"))
from test_openrouter_catalog import agent_values, catalog, render


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer-source", required=True, type=Path)
    parser.add_argument("--consumer-python", required=True, type=Path)
    args = parser.parse_args()
    payloads = []
    for version, forwarding in ((1, "none"), (2, "none"),
                                (2, "if-no-pii-detected"), (2, "pii-unchecked")):
        processed = {"name": "processed", "local": True, "model": "vision",
                     "attachmentMode": "extract" if version == 1 else "process"}
        if version == 2:
            processed.update(imageForwarding=forwarding,
                             faceProtectionEnabled=forwarding != "pii-unchecked")
        values = agent_values(catalog(), [
            {"name": "passthrough", "provider": "OpenAI", "model": "native",
             "baseURL": "https://provider.example.test/v1", "piiEnabled": False,
             "attachmentMode": "passthrough"}, processed,
        ])
        values["guardrails"]["llmPolicyEngine"]["attachmentPolicyVersion"] = version
        values["authKeycloak"]["agentgatewayClientRoles"] += [
            "model:passthrough:invoke", "model:processed:invoke",
        ]
        values["docling"] = {"enabled": True, "inference": {"mode": "private-vlm"}}
        rendered = render("agentgateway", values).stdout
        # Literal JSON CEL expressions are decoded; only the verified-identity
        # expression is substituted with a synthetic principal (not caller input).
        block = re.search(r"neurwerk\.destination_policy:\n((?: {16}[^\n]*\n)+)", rendered)
        assert block is not None
        fields = re.findall(r"(?m)^                ([a-z_]+): (.+)$", block.group(1))
        payload = {}
        for name, expression in fields:
            if name == "principal_id":
                payload[name] = "synthetic-principal"
            else:
                cel = expression[1:-1] if expression.startswith("'") else json.loads(expression)
                payload[name] = json.loads(cel)
        assert payload["destination_kind"] == "model"
        assert len(payload["models"]) == 3
        payloads.append(payload)

    # Run the actual consumer, not a copied schema; -B prevents external bytecode writes.
    subprocess.run([str(args.consumer_python.absolute()), "-B", "-c", '''
import hashlib, json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from agentgateway_extproc.gen import ext_proc_pb2
from agentgateway_extproc.models import destination
from agentgateway_extproc.models.exceptions import TrustedMetadataError

def parse(payload):
    request = ext_proc_pb2.ProcessingRequest()
    request.metadata_context.filter_metadata[destination.DESTINATION_POLICY_NAMESPACE].update(payload)
    return destination.destination_policy_from_request(request)

payloads = json.load(sys.stdin)
for payload in payloads:
    policy = parse(payload)
    assert policy.attachment_modes["passthrough"] == "passthrough"
    assert policy.models["passthrough"] is False
    assert len(policy.models) == 3
    if payload["contract_version"] == 2:
        assert "passthrough" not in policy.image_forwarding
        assert policy.protects_faces("passthrough") is False
        payload["image_forwarding"]["passthrough"] = "none"
        try:
            parse(payload)
        except TrustedMetadataError:
            pass
        else:
            raise AssertionError("consumer accepted the original passthrough regression")
source = pathlib.Path(destination.__file__)
print("PASS: four rendered mixed catalogs accepted by actual protobuf consumer parser;")
print("all three v2 passthrough forwarding regressions rejected.")
print("Consumer destination.py SHA256:", hashlib.sha256(source.read_bytes()).hexdigest())
''', str(args.consumer_source.absolute() / "src")], input=json.dumps(payloads), text=True, check=True)


if __name__ == "__main__":
    main()
