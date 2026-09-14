"""Disposable CLI contract fixture; not a substitute for image acceptance."""

import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["MOCK_STATE"])
state = json.loads(state_path.read_text())
args = sys.argv[5:]
state["calls"].append(args)
state_path.write_text(json.dumps(state))
failure = os.environ.get("MOCK_FAILURE", "")

if args == ["migrate"]:
    sys.exit(1 if failure == "migrate" else 0)
if args[:3] == ["admin", "auth", "list"]:
    if failure == "auth-list":
        sys.exit(1)
    print("ID | Name | Type | Enabled")
    print("\n".join(state["auth"]))
elif args[:2] == ["admin", "auth"] and args[2] in ["add-oauth", "update-oauth"]:
    if failure == "auth-write":
        sys.exit(1)
    if failure != "auth-readback":
        state["auth"] = ["1 | keycloak | OAuth2 | true"]
elif args == ["admin", "user", "list"]:
    if failure == "user-list":
        sys.exit(1)
    print("ID Username Email IsActive IsAdmin 2FA")
    print("\n".join(state["users"]))
elif args[:3] == ["admin", "user", "create"]:
    if failure == "user-write":
        sys.exit(1)
    if failure != "user-readback":
        state["users"].append("2 forgejo-recovery synthetic true true false")
else:
    raise AssertionError(args)
state_path.write_text(json.dumps(state))
