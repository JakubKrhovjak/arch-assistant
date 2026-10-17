#!/bin/bash
# Check: space the package cleanup would actually free (old versions beyond 2, uninstalled packages,
# yay build cache) above AA_PKG_CACHE_GB. The cache of currently installed versions is kept on purpose
# (downgrade / offline reinstall), so its total size alone is not a finding.
set -uo pipefail

limit_mb=$(python3 -c "print(int(float('${AA_PKG_CACHE_GB:-2}') * 1024))")
pacman_mb=$(du -sm /var/cache/pacman/pkg 2>/dev/null | awk '{print $1; exit}')
yay_mb=$(du -sm "$HOME/.cache/yay" 2>/dev/null | awk '{print $1; exit}')
old_versions=$(paccache -dk2 2>/dev/null | tail -n1)
uninstalled=$(paccache -duk0 2>/dev/null | tail -n1)

PACMAN_MB="${pacman_mb:-0}" YAY_MB="${yay_mb:-0}" OLD="$old_versions" UNINST="$uninstalled" LIMIT="$limit_mb" python3 - <<'PY'
import json, os, re

def saved_mb(line):
    m = re.search(r"disk space saved: ([\d.]+) (B|KiB|MiB|GiB|TiB)", line or "")
    if not m:
        return 0
    factor = {"B": 1 / 1048576, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "TiB": 1048576}[m.group(2)]
    return round(float(m.group(1)) * factor)

pacman_mb, yay_mb = int(os.environ["PACMAN_MB"]), int(os.environ["YAY_MB"])
items = [i for i in (
    {"name": "old package versions", "size_mb": saved_mb(os.environ["OLD"])},
    {"name": "uninstalled packages", "size_mb": saved_mb(os.environ["UNINST"])},
    {"name": "yay build cache", "size_mb": yay_mb},
) if i["size_mb"] > 0]
total = sum(i["size_mb"] for i in items)
if total <= int(os.environ["LIMIT"]):
    print(json.dumps({"id": "pkg-cache", "severity": "none",
                      "title": f"Package cache {pacman_mb} MB, {total} MB reclaimable", "summary": ""}))
    raise SystemExit
print(json.dumps({
    "id": "pkg-cache", "severity": "low",
    "title": f"Package caches: {total} MB can be freed",
    "summary": ", ".join(f"{i['name']} {i['size_mb']} MB" for i in items)
               + f" (pacman cache {pacman_mb} MB in total; installed versions are kept)",
    "items": items,
    "actions": [{"id": "clean-pkg-cache", "label": "Clean package caches", "args": []}],
}))
PY
