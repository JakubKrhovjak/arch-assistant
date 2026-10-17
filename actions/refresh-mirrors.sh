#!/bin/bash
# Action: regenerate the pacman mirrorlist with reflector. Env: AA_MIRRORLIST (default /etc/pacman.d/mirrorlist).
set -uo pipefail

ml="${AA_MIRRORLIST:-/etc/pacman.d/mirrorlist}"
err=$(sudo /usr/bin/reflector --country Czechia,Germany --protocol https --latest 20 --sort rate --save "$ml" 2>&1 >/dev/null)
rc=$?
if [[ $rc -ne 0 ]]; then
    echo "$err" >&2
    echo "Mirrorlist refresh failed: $(head -n1 <<<"$err")"
    exit 1
fi
count=$(grep -c '^Server' "$ml" 2>/dev/null || true)
echo "Mirrorlist refreshed (${count:-0} servers)"
