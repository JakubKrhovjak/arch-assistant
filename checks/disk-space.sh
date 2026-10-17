#!/bin/bash
# Check: free space on /, /home, /mnt/data below AA_DISK_PCT %, and on the ESP (/boot/efi or /efi) below AA_ESP_MB.
set -uo pipefail

limit_pct="${AA_DISK_PCT:-10}"
esp_mb="${AA_ESP_MB:-30}"

mounts=()
for m in / /home /boot/efi /efi /mnt/data; do
    [[ -d "$m" ]] && mountpoint -q "$m" 2>/dev/null && mounts+=("$m")
done
[[ ${#mounts[@]} -eq 0 ]] && mounts=(/)

low=$(df --output=target,pcent,avail -B1M "${mounts[@]}" 2>/dev/null | tail -n +2 | awk -v limit="$limit_pct" -v esp="$esp_mb" '
    {
        target = $1; used = $2; sub("%", "", used); avail = $3 + 0
        free_pct = 100 - used
        is_esp = (target == "/boot/efi" || target == "/efi")
        if ((is_esp && avail < esp) || (!is_esp && free_pct < limit)) print target, free_pct, avail
    }')

LOW="$low" python3 - <<'PY'
import json, os
items = [{"name": t, "free_pct": int(p), "free_mb": int(a)} for t, p, a in (l.split() for l in os.environ["LOW"].splitlines() if l.strip())]
if not items:
    print(json.dumps({"id": "disk-space", "severity": "none", "title": "Disk space OK", "summary": ""}))
    raise SystemExit
desc = ", ".join(f"{i['name']}: {i['free_mb']} MB free ({i['free_pct']} %)" for i in items)
print(json.dumps({"id": "disk-space", "severity": "high",
                  "title": f"Low disk space on {len(items)} filesystem{'s' if len(items) != 1 else ''}",
                  "summary": desc, "items": items, "actions": []}))
PY
