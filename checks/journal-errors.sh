#!/bin/bash
# Check: number of priority<=err journal entries since boot above AA_JOURNAL_ERR; top 10 messages.
set -uo pipefail

limit="${AA_JOURNAL_ERR:-50}"
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
journalctl -b -p err --output=json --no-pager > "$tmp" 2>/dev/null

LIMIT="$limit" python3 - "$tmp" <<'PY'
import json, os, sys
from collections import Counter
counts = Counter()
total = 0
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line).get("MESSAGE", "")
    except json.JSONDecodeError:
        continue
    if isinstance(msg, list):  # binary message comes as a byte array
        msg = bytes(b for b in msg if isinstance(b, int)).decode("utf-8", "replace")
    total += 1
    counts[str(msg)[:120]] += 1
limit = int(os.environ["LIMIT"])
title = f"{total} error{'s' if total != 1 else ''} in the journal since boot"
if total <= limit:
    print(json.dumps({"id": "journal-errors", "severity": "none", "title": title, "summary": ""}))
    raise SystemExit
items = [{"name": m, "count": c} for m, c in counts.most_common(10)]
print(json.dumps({
    "id": "journal-errors", "severity": "low", "title": title,
    "summary": "; ".join(f"{i['name'][:60]} x{i['count']}" for i in items[:3]),
    "items": items, "actions": [],
}))
PY
