#!/bin/bash
# Action: open pacdiff in a terminal so the user can merge .pacnew files interactively. Never runs unattended.
set -uo pipefail

# the terminal comes from arch_assistant/actions.py (TERMINAL), passed as AA_TERMINAL
term="${AA_TERMINAL:-}"
if [[ -z $term ]] || ! command -v "$term" >/dev/null 2>&1; then
    echo "Terminal '${term:-(AA_TERMINAL unset)}' not found (set AA_TERMINAL)"
    exit 1
fi
setsid "$term" -e sudo /usr/bin/pacdiff >/dev/null 2>&1 &
sleep 0.3
echo "Opened pacdiff in a terminal"
