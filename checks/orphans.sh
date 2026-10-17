#!/bin/bash
# Check: orphan packages (installed as dependencies, no longer required).
set -uo pipefail

orphans=$(pacman -Qdtq 2>/dev/null || true)

ORPHANS="$orphans" python3 - <<'PY'
import json, os
names = [n.strip() for n in os.environ["ORPHANS"].splitlines() if n.strip()]
if not names:
    print(json.dumps({"id": "orphans", "severity": "none", "title": "No orphan packages", "summary": ""}))
    raise SystemExit
n = len(names)
print(json.dumps({
    "id": "orphans", "severity": "low",
    "title": f"{n} orphan package{'s' if n != 1 else ''}",
    "summary": ", ".join(names[:15]) + (f", … ({n} total)" if n > 15 else ""),
    "items": [{"name": x} for x in names],
    "actions": [{"id": "remove-orphans", "label": "Remove orphan packages", "args": names}],
}))
PY
