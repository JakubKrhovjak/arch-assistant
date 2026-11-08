#!/bin/bash
# Check: the LocalSend user service (localsend.service from the setup, Restart=always) is running, so the phone can
# send files any time. Reports nothing when the service is not installed.
set -uo pipefail

UNIT=localsend.service

state=$(systemctl --user show "$UNIT" -p LoadState -p ActiveState -p SubState 2>/dev/null)
load=$(sed -n 's/^LoadState=//p' <<<"$state")
active=$(sed -n 's/^ActiveState=//p' <<<"$state")
sub=$(sed -n 's/^SubState=//p' <<<"$state")

if [[ -z "$load" || "$load" == "not-found" || "$load" == "masked" ]]; then
    echo '{"id": "localsend", "severity": "none", "title": "LocalSend service not installed", "summary": ""}'
    exit 0
fi
if [[ "$active" == "active" ]]; then
    echo '{"id": "localsend", "severity": "none", "title": "LocalSend is running", "summary": ""}'
    exit 0
fi

python3 - "$UNIT" "$active" "$sub" <<'PY'
import json, sys
unit, active, sub = sys.argv[1:4]
print(json.dumps({
    "id": "localsend", "severity": "medium",
    "title": "LocalSend is not running",
    "summary": f"{unit} is {active} ({sub}): the phone cannot send files to this computer",
    "items": [{"name": unit, "state": active, "sub": sub}],
    "actions": [{"id": "restart-localsend", "label": "Restart LocalSend", "args": []}],
}))
PY
