#!/bin/bash
# Check: Timeshift has a snapshot and the latest one is at most 2 days old.
set -uo pipefail

MAX_AGE_DAYS=2
listing=$(sudo /usr/bin/timeshift --list 2>/dev/null || true)

LISTING="$listing" MAX_AGE="$MAX_AGE_DAYS" python3 - <<'PY'
import json, os, re
from datetime import datetime
listing = os.environ["LISTING"]
names = re.findall(r"^\s*\d+\s+>?\s*(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})", listing, re.MULTILINE)
status = (re.search(r"^Status\s*:\s*(.+)$", listing, re.MULTILINE) or [None, "unknown"])[1].strip()
if not names:
    print(json.dumps({"id": "timeshift", "severity": "medium", "title": "No Timeshift snapshots",
                      "summary": f"No snapshots found (status: {status})", "items": [], "actions": []}))
    raise SystemExit
latest = max(names)
age_days = (datetime.now() - datetime.strptime(latest, "%Y-%m-%d_%H-%M-%S")).total_seconds() / 86400
if age_days <= int(os.environ["MAX_AGE"]) and status == "OK":
    print(json.dumps({"id": "timeshift", "severity": "none", "title": "Timeshift snapshots are recent", "summary": ""}))
    raise SystemExit
title = f"Last Timeshift snapshot is {int(age_days)} days old" if age_days > int(os.environ["MAX_AGE"]) else f"Timeshift status: {status}"
print(json.dumps({"id": "timeshift", "severity": "medium", "title": title,
                  "summary": f"latest {latest}, {len(names)} snapshots, status {status}",
                  "items": [{"name": latest, "age_days": int(age_days), "status": status}], "actions": []}))
PY
