#!/bin/bash
# Diagnose collector: status and recent journal of every unit in the finding (finding JSON on stdin).
set -uo pipefail

units=$(python3 -c 'import json,sys; [print(i["scope"], i["name"]) for i in json.load(sys.stdin).get("items", [])]')
while read -r scope name; do
    [[ -z "$name" ]] && continue
    flag=""
    [[ "$scope" == "user" ]] && flag="--user"
    echo "===== $name ($scope) ====="
    # shellcheck disable=SC2086
    systemctl $flag status "$name" --no-pager -n 0 2>&1 | head -20
    echo "--- journal (last 40 lines)"
    # shellcheck disable=SC2086
    journalctl $flag -u "$name" -n 40 --no-pager -o short 2>&1 | cut -c1-300
    echo
done <<<"$units"
