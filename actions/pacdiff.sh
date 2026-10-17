#!/bin/bash
# Action: open pacdiff in a terminal so the user can merge .pacnew files interactively. Never runs unattended.
set -uo pipefail

term="${AA_TERMINAL:-ghostty}"
if ! command -v "$term" >/dev/null 2>&1; then
    echo "Terminal '$term' not found (set AA_TERMINAL)"
    exit 1
fi
setsid "$term" -e sudo /usr/bin/pacdiff >/dev/null 2>&1 &
sleep 0.3
echo "Opened pacdiff in a terminal"
