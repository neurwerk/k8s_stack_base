"""Opt-in Helm -> protobuf -> real consumer validation, without network calls.

Pass an explicit consumer source tree and its installed Python environment.
No sibling-repository layout or consumer version is assumed by normal Base tests.
The v4 fixture is the producer-side schema expected by the coordinated extProc task.
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

    def rendered_payload(values):
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
        return payload

    for version, forwarding in ((1, "none"), (2, "none"),
                                (2, "if-no-pii-detected"), (2, "pii-unchecked"),
                                 (3, "none"), (3, "if-no-pii-detected"), (3, "pii-unchecked"),
                                 (3, "if-policy-allows")):
        processed = {"name": "processed", "local": True, "model": "vision",
                     "attachmentMode": "extract" if version == 1 else "process"}
        if version >= 2:
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
        if version == 3:
            processed["supportsImages"] = True
            values["guardrails"]["llmPolicyEngine"]["localTarget"]["supportsImages"] = True
            values["docling"]["inference"]["mode"] = "internal-standard"
            source = values["openrouterCatalog"]["models"][0]
            source.update(attachmentMode="process", imageForwarding=(
                "if-no-pii-detected" if forwarding == "pii-unchecked" else forwarding
            ))
            policy = values["monitorPiiEngine"]["policy"]
            policy["attachments"] = {"faces": {"action": "reroute", "routeClass": "faces/local"}}
            if forwarding in {"if-no-pii-detected", "if-policy-allows"}:
                policy["routing"]["targets"] = [{"name": "processed", "classPrefix": "faces/"}]
        payload = rendered_payload(values)
        assert len(payload["models"]) == 3
        payloads.append(payload)

    typed_models = [
        {"name": "blocked", "local": True, "model": "text", "attachments": {}},
        {"name": "document", "local": True, "model": "text",
         "attachments": {"documents": {"mode": "extract-text"}}},
        {"name": "image-text", "local": True, "model": "vision", "piiEnabled": False,
         "attachments": {"images": {"mode": "extract-text"}}},
        {"name": "enforce", "local": True, "model": "vision", "supportsImages": True,
         "attachments": {"images": {"mode": "forward-normalized"}}},
        {"name": "strict", "local": True, "model": "vision", "supportsImages": True,
         "attachments": {"images": {"mode": "forward-normalized", "policy": "strict"}}},
        {"name": "unchecked", "local": True, "model": "vision", "supportsImages": True,
         "piiEnabled": False,
         "attachments": {"images": {"mode": "forward-normalized", "policy": "unchecked"}}},
    ]
    typed_catalog = catalog()
    typed_catalog["models"][0]["attachments"] = {}
    typed_values = agent_values(typed_catalog, typed_models)
    typed_values["guardrails"]["llmPolicyEngine"]["attachmentPolicyVersion"] = 4
    typed_values["authKeycloak"]["agentgatewayClientRoles"] += [
        f"model:{model['name']}:invoke" for model in typed_models
    ]
    typed_values["docling"] = {"enabled": True, "inference": {"mode": "internal-standard"}}
    payloads.append(rendered_payload(typed_values))

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
    if payload["contract_version"] == 4:
        assert "attachment_modes" not in payload
        assert len(policy.models) == 7
        assert policy.document_modes == {
            "blocked": "block", "document": "extract-text", "enforce": "block",
            "image-text": "block", "remote/openrouter/acme/model": "block",
            "strict": "block", "unchecked": "block",
        }
        assert policy.image_modes == {
            "blocked": "block", "document": "block", "enforce": "forward-normalized",
            "image-text": "extract-text", "remote/openrouter/acme/model": "block",
            "strict": "forward-normalized", "unchecked": "forward-normalized",
        }
        assert policy.image_forwarding["enforce"] == "if-policy-allows"
        assert policy.image_forwarding["strict"] == "if-no-pii-detected"
        assert policy.image_forwarding["unchecked"] == "pii-unchecked"
        assert policy.protects_faces("document") is True
        assert policy.protects_faces("image-text") is True
        assert policy.protects_faces("enforce") is True
        assert policy.protects_faces("strict") is True
        assert policy.protects_faces("unchecked") is False
        assert policy.image_models["enforce"] is True
        assert policy.image_models["strict"] is True
        assert policy.image_models["unchecked"] is True
        assert policy.image_reroutes == {}
        for invalid in (
            {**payload, "attachment_modes": {}},
            {key: value for key, value in payload.items() if key != "document_modes"},
            {key: value for key, value in payload.items() if key != "image_modes"},
        ):
            try:
                parse(invalid)
            except TrustedMetadataError:
                pass
            else:
                raise AssertionError("consumer accepted an invalid v4 typed attachment contract")
        continue
    assert policy.attachment_modes["passthrough"] == "passthrough"
    assert policy.models["passthrough"] is False
    assert len(policy.models) == 3
    if payload["contract_version"] == 3:
        assert policy.image_models == {"passthrough": False, "processed": True,
                                       "remote/openrouter/acme/model": False}
        forwarding = policy.image_forwarding["processed"]
        expected = {}
        if forwarding != "none":
            target = ("processed" if forwarding in {"if-no-pii-detected", "if-policy-allows"}
                      else "remote-openrouter-acme-model-local")
            expected = {"processed": {"faces/local": "processed"},
                        "remote/openrouter/acme/model": {"faces/local": target}}
        assert policy.image_reroutes == expected
        if forwarding == "if-policy-allows":
            assert policy.image_forwarding["remote/openrouter/acme/model"] == forwarding
            for version in (1, 2):
                invalid = {**payload, "contract_version": version}
                for field in ("image_models", "image_reroutes"):
                    invalid.pop(field)
                try:
                    parse(invalid)
                except TrustedMetadataError:
                    pass
                else:
                    raise AssertionError("consumer accepted policy-aware images under an old version")
        if forwarding == "pii-unchecked":
            invalid = {**payload, "image_models": {**payload["image_models"], "processed": False}}
            try:
                parse(invalid)
            except TrustedMetadataError:
                pass
            else:
                raise AssertionError("consumer accepted unchecked images without image capability")
    else:
        for field in ("image_models", "image_reroutes"):
            try:
                parse({**payload, field: {}})
            except TrustedMetadataError:
                pass
            else:
                raise AssertionError("consumer accepted a v3 field under an old version")
    if payload["contract_version"] >= 2:
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
print("PASS: nine rendered v1-v4 catalogs accepted by actual protobuf consumer parser;")
print("typed modes and exact local image bindings preserved; version/capability regressions rejected.")
print("Consumer destination.py SHA256:", hashlib.sha256(source.read_bytes()).hexdigest())
''', str(args.consumer_source.absolute() / "src")], input=json.dumps(payloads), text=True, check=True)


if __name__ == "__main__":
    main()
