#!/bin/bash
# Check: entries in ~/.cache larger than AA_USER_CACHE_GB, plus the safe-to-delete ones when together they exceed it. Env: AA_HOME.
set -uo pipefail

home="${AA_HOME:-$HOME}"
cache="$home/.cache"
limit_mb=$(python3 -c "print(int(float('${AA_USER_CACHE_GB:-1}') * 1024))")
listing=$(du -d1 -m "$cache" 2>/dev/null || true)

CACHE="$cache" LISTING="$listing" LIMIT="$limit_mb" python3 - "$(dirname "$0")" <<'PY'
import json, os, sys
sys.path[:0] = [sys.argv[1]]
from _caches import SAFE_CACHES
cache, limit = os.path.normpath(os.environ["CACHE"]), int(os.environ["LIMIT"])
entries = []
for line in os.environ["LISTING"].splitlines():
    try:
        size, path = line.split("\t", 1)
    except ValueError:
        continue
    path = os.path.normpath(path)
    if path == cache:
        continue
    name = os.path.basename(path)
    entries.append({"name": name, "size_mb": int(size), "safe": name in SAFE_CACHES})
combined = sum(e["size_mb"] for e in entries if e["safe"]) > limit
items = [e for e in entries if e["size_mb"] > limit or (combined and e["safe"] and e["size_mb"] > 0)]
items.sort(key=lambda i: -i["size_mb"])
if not items:
    print(json.dumps({"id": "user-cache", "severity": "none", "title": "No large caches", "summary": ""}))
    raise SystemExit
safe = [i["name"] for i in items if i["safe"]]
total = sum(i["size_mb"] for i in items)
print(json.dumps({
    "id": "user-cache", "severity": "low",
    "title": f"Large caches in ~/.cache: {total} MB",
    "summary": ", ".join(f"{i['name']} {i['size_mb']} MB" + ("" if i["safe"] else " (keep)") for i in items),
    "items": items,
    "actions": [{"id": "clean-user-cache", "label": "Delete safe caches", "args": safe}] if safe else [],
}))
PY
