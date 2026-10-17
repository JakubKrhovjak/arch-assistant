#!/bin/bash
# Check: the kernel clocksource is tsc (after a hard reset it may fall back to hpet and stall VMs).
set -uo pipefail

f="${AA_CLOCKSOURCE_FILE:-/sys/devices/system/clocksource/clocksource0/current_clocksource}"
cs=$(cat "$f" 2>/dev/null | tr -d '[:space:]')

CS="${cs:-unknown}" python3 - <<'PY'
import json, os
cs = os.environ["CS"]
if cs == "tsc":
    print(json.dumps({"id": "clocksource", "severity": "none", "title": "Clocksource is tsc", "summary": ""}))
else:
    print(json.dumps({
        "id": "clocksource", "severity": "medium",
        "title": f"Clocksource is {cs}, not tsc",
        "summary": (f"The kernel switched to {cs} (unstable TSC, usually after a hard reset); the system and VMs may stutter. "
                    "A full power off and on usually restores tsc; permanent fix: kernel parameter tsc=reliable."),
        "items": [{"name": cs}], "actions": [],
    }))
PY
