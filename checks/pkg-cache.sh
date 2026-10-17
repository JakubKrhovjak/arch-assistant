#!/bin/bash
# Check: pacman + yay package caches above AA_PKG_CACHE_GB in total.
set -uo pipefail

limit_mb=$(python3 -c "print(int(float('${AA_PKG_CACHE_GB:-2}') * 1024))")
listing=$(du -sm /var/cache/pacman/pkg "$HOME/.cache/yay" 2>/dev/null || true)

LISTING="$listing" LIMIT="$limit_mb" python3 - <<'PY'
import json, os
items = []
for line in os.environ["LISTING"].splitlines():
    try:
        size, path = line.split("\t", 1)
        items.append({"name": path.strip(), "size_mb": int(size)})
    except ValueError:
        continue
total = sum(i["size_mb"] for i in items)
if total <= int(os.environ["LIMIT"]):
    print(json.dumps({"id": "pkg-cache", "severity": "none", "title": f"Package caches use {total} MB", "summary": ""}))
    raise SystemExit
print(json.dumps({
    "id": "pkg-cache", "severity": "low",
    "title": f"Package caches use {total} MB",
    "summary": ", ".join(f"{i['name']} {i['size_mb']} MB" for i in items), "items": items,
    "actions": [{"id": "clean-pkg-cache", "label": "Clean package caches", "args": []}],
}))
PY
