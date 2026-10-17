#!/bin/bash
# Check: pacman mirrorlist older than AA_MIRROR_DAYS days. Env: AA_MIRRORLIST (default /etc/pacman.d/mirrorlist).
set -uo pipefail

ml="${AA_MIRRORLIST:-/etc/pacman.d/mirrorlist}"
limit="${AA_MIRROR_DAYS:-7}"
mtime=$(stat -c %Y "$ml" 2>/dev/null || echo 0)
now=$(date +%s)
age_days=$(( (now - mtime) / 86400 ))
timer=$(systemctl is-active reflector.timer 2>/dev/null || true)

ML="$ml" AGE="$age_days" LIMIT="$limit" TIMER="${timer:-unknown}" python3 - <<'PY'
import json, os
age, limit = int(os.environ["AGE"]), int(os.environ["LIMIT"])
if age <= limit:
    print(json.dumps({"id": "mirrors", "severity": "none", "title": "Mirrorlist is fresh", "summary": ""}))
    raise SystemExit
timer = os.environ["TIMER"].strip()
print(json.dumps({
    "id": "mirrors", "severity": "low",
    "title": f"Mirrorlist is {age} days old",
    "summary": f"{os.environ['ML']} last refreshed {age} days ago; reflector.timer is {timer}",
    "items": [{"name": os.environ["ML"], "age_days": age, "timer": timer}],
    "actions": [{"id": "refresh-mirrors", "label": "Refresh mirrorlist", "args": []}],
}))
PY
