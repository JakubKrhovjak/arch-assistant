#!/bin/bash
# Diagnose collector: details of the processes named in the finding.
set -uo pipefail
pids=$(python3 -c 'import json,sys; [print(i["pid"]) for i in json.load(sys.stdin).get("items", []) if "pid" in i]')
echo "===== processes"
for pid in $pids; do
    ps -o pid,ppid,user,etimes,pcpu,pmem,rss,nlwp,stat,args -p "$pid" --no-headers 2>/dev/null | cut -c1-240
    echo "  cgroup: $(cat "/proc/$pid/cgroup" 2>/dev/null | head -1)"
done
echo "===== top 10 by CPU now"
ps -eo pid,comm,pcpu,rss,etimes --sort=-pcpu --no-headers | head -10
echo "===== memory"
free -m
