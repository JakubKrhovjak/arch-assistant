#!/bin/bash
# Check: failed systemd units (system + user) and running services restarting in a loop (NRestarts >= 5).
set -uo pipefail

RESTART_LIMIT=5

failed_units() {   # $1 = "" | "--user"
    # shellcheck disable=SC2086
    systemctl $1 --failed --plain --no-legend 2>/dev/null | awk '{print $1}'
}

restarting_units() {   # $1 = "" | "--user"
    local scope="$1" units
    # shellcheck disable=SC2086
    mapfile -t units < <(systemctl $scope list-units --type=service --state=running --plain --no-legend 2>/dev/null | awk '{print $1}')
    [[ ${#units[@]} -eq 0 ]] && return 0
    # shellcheck disable=SC2086
    systemctl $scope show -p Id,NRestarts "${units[@]}" 2>/dev/null | awk -v limit="$RESTART_LIMIT" '
        /^Id=/ { id = substr($0, 4) }
        /^NRestarts=/ { n = substr($0, 11) + 0; if (n >= limit) print id, n }'
}

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
{
    failed_units ""       | awk '{print "system failed " $1 " 0"}'
    failed_units "--user" | awk '{print "user failed " $1 " 0"}'
    restarting_units ""       | awk '{print "system restarting " $1 " " $2}'
    restarting_units "--user" | awk '{print "user restarting " $1 " " $2}'
} > "$tmp"

python3 - "$tmp" <<'PY'
import json, sys
items, actions = [], []
for line in open(sys.argv[1]):
    scope, state, name, restarts = line.split()
    items.append({"name": name, "scope": scope, "state": state, "restarts": int(restarts)})
    actions.append({"id": "restart-unit", "label": f"Restart {name}", "args": [scope, name]})
if not items:
    print(json.dumps({"id": "failed-units", "severity": "none", "title": "All systemd units healthy", "summary": ""}))
    raise SystemExit
n = len(items)
def describe(i):
    extra = f" x{i['restarts']}" if i["state"] == "restarting" else ""
    return f"{i['name']} ({i['scope']}, {i['state']}{extra})"
print(json.dumps({
    "id": "failed-units", "severity": "high",
    "title": f"{n} systemd unit{'s' if n != 1 else ''} failed or restarting",
    "summary": ", ".join(describe(i) for i in items), "items": items, "actions": actions,
}))
PY
