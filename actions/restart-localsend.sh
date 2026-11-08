#!/bin/bash
# Action: start the LocalSend user service again. reset-failed first: after a crash loop systemd refuses to start it
# (start-limit-hit) until the failed state is cleared. Takes no args.
set -uo pipefail

UNIT=localsend.service

systemctl --user reset-failed "$UNIT" >/dev/null 2>&1
err=$(systemctl --user restart "$UNIT" 2>&1 >/dev/null)
rc=$?
if [[ $rc -ne 0 ]]; then
    echo "$err" >&2
    echo "Restart of LocalSend failed: $(head -n1 <<<"$err")"
    exit 1
fi
echo "LocalSend restarted"
