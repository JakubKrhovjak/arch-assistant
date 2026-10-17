#!/bin/bash
# Check: running kernel differs from the installed package, or core packages were installed after boot.
set -uo pipefail

running=$(uname -r)
pkg=linux
running_cmp="$running"
if [[ "$running" == *-lts ]]; then
    pkg=linux-lts
    running_cmp="${running%-lts}"
fi
installed=$(pacman -Q "$pkg" 2>/dev/null | awk '{print $2}')
boot_epoch=$(date -d "$(uptime -s)" +%s 2>/dev/null || echo 0)

after_boot=""
for p in systemd mesa qt6-base; do
    d=$(LC_ALL=C pacman -Qi "$p" 2>/dev/null | awk -F': *' '/^Install Date/{print $2}')
    [[ -z "$d" ]] && continue
    e=$(date -d "$d" +%s 2>/dev/null || echo 0)
    if [[ "$e" -gt "$boot_epoch" ]]; then
        after_boot+="$p|$d"$'\n'
    fi
done

PKG="$pkg" RUNNING="$running" RUNNING_CMP="$running_cmp" INSTALLED="$installed" AFTER_BOOT="$after_boot" python3 - <<'PY'
import json, os
# pacman prints 7.2.10.arch1-1, uname prints 7.2.10-arch1-1: compare with separators unified
norm = lambda v: v.translate(str.maketrans("-", "."))
pkg, running, installed = os.environ["PKG"], os.environ["RUNNING"], os.environ["INSTALLED"]
items, reasons = [], []
if installed and norm(os.environ["RUNNING_CMP"]) != norm(installed):
    items.append({"name": pkg, "running": running, "installed": installed})
    reasons.append(f"kernel {installed} installed, running {running}")
for line in os.environ["AFTER_BOOT"].splitlines():
    name, date = line.split("|", 1)
    items.append({"name": name, "installed_at": date})
    reasons.append(f"{name} updated after boot")
if not items:
    print(json.dumps({"id": "reboot-needed", "severity": "none", "title": "No reboot needed", "summary": ""}))
else:
    print(json.dumps({"id": "reboot-needed", "severity": "medium", "title": "Reboot needed",
                      "summary": "; ".join(reasons), "items": items, "actions": []}))
PY
