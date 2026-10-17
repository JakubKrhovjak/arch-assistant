#!/bin/bash
# Check: pending repo (checkupdates) and AUR (yay -Qua) updates.
# Env: AA_CRITICAL_PACKAGES (space-separated fnmatch patterns).
set -uo pipefail

repo_out=$(checkupdates 2>/tmp/aa-updates-err.$$)
rc=$?
err=$(cat /tmp/aa-updates-err.$$ 2>/dev/null); rm -f /tmp/aa-updates-err.$$
# checkupdates: 0 = updates listed, 2 = nothing to do, anything else = error
if [[ $rc -ne 0 && $rc -ne 2 ]]; then
    echo "checkupdates failed (exit $rc): $err" >&2
    exit 1
fi

aur_out=""
if command -v yay >/dev/null 2>&1; then
    aur_out=$(yay -Qua 2>/dev/null || true)
fi

REPO_OUT="$repo_out" AUR_OUT="$aur_out" python3 - <<'PY'
import json, os
from fnmatch import fnmatch

patterns = os.environ.get("AA_CRITICAL_PACKAGES", "").split()

def parse(text, source):
    for line in text.splitlines():
        parts = line.split()
        # "name old -> new" (AUR lines may be prefixed with "aur/" and suffixed with "[age]")
        if len(parts) < 4 or parts[2] != "->":
            continue
        name = parts[0].split("/")[-1]
        yield {"name": name, "old": parts[1], "new": parts[3], "source": source,
               "critical": any(fnmatch(name, p) for p in patterns)}

items = list(parse(os.environ["REPO_OUT"], "repo")) + list(parse(os.environ["AUR_OUT"], "aur"))
if not items:
    print(json.dumps({"id": "updates", "severity": "none", "title": "No pending updates", "summary": ""}))
    raise SystemExit

critical = [i for i in items if i["critical"]]
others = len(items) - len(critical)
if critical:
    desc = ", ".join(f"{i['name']} {i['old']} -> {i['new']}" for i in critical)
    tail = f"; {others} other package{'s' if others != 1 else ''}" if others else ""
    summary = f"Critical: {desc}{tail}"
    severity = "high"
else:
    summary = f"{len(items)} package{'s' if len(items) != 1 else ''}"
    severity = "low"

print(json.dumps({
    "id": "updates", "severity": severity,
    "title": f"{len(items)} pending update{'s' if len(items) != 1 else ''}",
    "summary": summary, "items": items,
    "actions": [{"id": "update", "label": "Update system", "args": []}],
}))
PY
