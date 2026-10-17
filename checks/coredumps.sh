#!/bin/bash
# Check: new core dumps since the last cycle ($AA_STATE_DIR/state.json -> last_cycle_at, else 7 days).
set -uo pipefail

state="${AA_STATE_DIR:-.}/state.json"
since=$(STATE="$state" python3 - <<'PY'
import json, os
try:
    print(json.load(open(os.environ["STATE"])).get("last_cycle_at") or "-7days")
except Exception:
    print("-7days")
PY
)

dumps=$(coredumpctl list --json=short --since "$since" 2>/dev/null || true)
total_mb=$(du -sm /var/lib/systemd/coredump 2>/dev/null | awk '{print $1}')

DUMPS="$dumps" TOTAL_MB="${total_mb:-0}" python3 - <<'PY'
import json, os
from collections import Counter
from pathlib import PurePath
try:
    dumps = json.loads(os.environ["DUMPS"] or "[]")
except json.JSONDecodeError:
    dumps = []
if not dumps:
    print(json.dumps({"id": "coredumps", "severity": "none", "title": "No new core dumps", "summary": ""}))
    raise SystemExit
counts = Counter(PurePath(d.get("exe", "?")).name for d in dumps)
items = [{"name": exe, "count": n} for exe, n in counts.most_common()]
items[0]["size_mb"] = int(os.environ["TOTAL_MB"] or 0)
n = len(dumps)
print(json.dumps({
    "id": "coredumps", "severity": "medium",
    "title": f"{n} new core dump{'s' if n != 1 else ''}, {items[0]['size_mb']} MB on disk",
    "summary": ", ".join(f"{i['name']} x{i['count']}" for i in items), "items": items,
    "actions": [{"id": "clean-coredumps", "label": "Remove core dumps", "args": []}],
}))
PY
