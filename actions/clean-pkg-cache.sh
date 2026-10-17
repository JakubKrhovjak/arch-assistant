#!/bin/bash
# Action: keep the last 2 versions in the pacman cache (paccache -rk2) and clean the yay cache.
set -uo pipefail

total_mb() {
    du -sm /var/cache/pacman/pkg "$HOME/.cache/yay" 2>/dev/null | awk '{s += $1} END {print s + 0}'
}

before=$(total_mb)
if ! sudo /usr/bin/paccache -rk2 1>&2; then
    echo "paccache failed"
    exit 1
fi
if command -v yay >/dev/null 2>&1; then
    yay -Sc --noconfirm 1>&2 || true
fi
after=$(total_mb)
echo "Freed $(( before - after )) MB (pacman cache kept 2 versions, yay cache cleaned)"
