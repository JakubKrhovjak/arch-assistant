#!/bin/bash
# Check: .pacnew / .pacsave files waiting to be merged (pacdiff -o).
set -uo pipefail

# shellcheck source-path=SCRIPTDIR source=_root.sh
source "$(dirname "${BASH_SOURCE[0]}")/_root.sh"

files=$(root /usr/bin/pacdiff -o)
exit_if_refused $?

FILES="$files" python3 - <<'PY'
import json, os
items = [{"name": f} for f in os.environ["FILES"].splitlines() if f.strip()]
if not items:
    print(json.dumps({"id": "pacnew", "severity": "none", "title": "No .pacnew files", "summary": ""}))
    raise SystemExit
n = len(items)
print(json.dumps({
    "id": "pacnew", "severity": "medium",
    "title": f"{n} .pacnew file{'s' if n != 1 else ''} to merge",
    "summary": ", ".join(i["name"] for i in items), "items": items,
    "actions": [{"id": "pacdiff", "label": "Review .pacnew files in a terminal", "args": []}],
}))
PY
