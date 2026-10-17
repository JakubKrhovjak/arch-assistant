#!/bin/bash
# Action: restart a systemd unit. Args: <system|user> <unit>.
set -uo pipefail

scope="${1:-}"
unit="${2:-}"
if [[ -z "$unit" || ( "$scope" != "system" && "$scope" != "user" ) ]]; then
    echo "Usage: restart-unit <system|user> <unit> (bad scope '$scope')"
    exit 2
fi

if [[ "$scope" == "user" ]]; then
    err=$(systemctl --user restart "$unit" 2>&1 >/dev/null)
else
    err=$(sudo /usr/bin/systemctl restart "$unit" 2>&1 >/dev/null)
fi
rc=$?
if [[ $rc -ne 0 ]]; then
    echo "$err" >&2
    echo "Restart of $unit failed: $(head -n1 <<<"$err")"
    exit 1
fi
echo "Unit $unit restarted"
