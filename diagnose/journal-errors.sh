#!/bin/bash
# Diagnose collector: the most frequent error messages since boot with their units.
set -uo pipefail
cat >/dev/null
echo "===== journal errors since boot, grouped (unit: message x count)"
journalctl -b -p err --output=json --no-pager 2>/dev/null | python3 -c '
import json, sys
from collections import Counter
c = Counter()
for line in sys.stdin:
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        continue
    msg = d.get("MESSAGE", "")
    if isinstance(msg, list):
        msg = "<binary>"
    c[(d.get("_SYSTEMD_UNIT") or d.get("SYSLOG_IDENTIFIER") or "?", str(msg)[:140])] += 1
for (unit, msg), n in c.most_common(25):
    print(f"{unit}: {msg} x{n}")
'
echo "===== last 30 error lines"
journalctl -b -p err -n 30 --no-pager -o short 2>/dev/null | cut -c1-240
