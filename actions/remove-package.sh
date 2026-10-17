#!/bin/bash
# Action: remove one package with its unneeded dependencies (pacman -Rns). Never runs unattended.
set -uo pipefail

pkg="${1:-}"
if [[ -z "$pkg" ]]; then
    echo "Usage: remove-package <package>"
    exit 2
fi
err=$(sudo /usr/bin/pacman -Rns --noconfirm "$pkg" 2>&1 >/dev/null)
rc=$?
if [[ $rc -ne 0 ]]; then
    echo "$err" >&2
    echo "Removing $pkg failed: $(grep -m1 -i error <<<"$err" || head -n1 <<<"$err")"
    exit 1
fi
echo "Removed package $pkg"
