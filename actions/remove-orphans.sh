#!/bin/bash
# Action: remove orphan packages given as arguments (pacman -Rns).
set -uo pipefail

if [[ $# -eq 0 ]]; then
    echo "Nothing to remove"
    exit 0
fi
err=$(sudo /usr/bin/pacman -Rns --noconfirm "$@" 2>&1 >/dev/null)
rc=$?
if [[ $rc -ne 0 ]]; then
    echo "$err" >&2
    echo "Removing orphans failed: $(grep -m1 -i error <<<"$err" || head -n1 <<<"$err")"
    exit 1
fi
plural=""
[[ $# -ne 1 ]] && plural="s"
list=$(printf '%s, ' "$@")
echo "Removed $# orphan package${plural}: ${list%, }"
