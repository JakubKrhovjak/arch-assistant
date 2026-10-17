#!/bin/bash
# Action: delete all stored core dumps in /var/lib/systemd/coredump.
set -uo pipefail

dir=/var/lib/systemd/coredump
before=$(du -sm "$dir" 2>/dev/null | awk '{print $1}')
count=$(sudo /usr/bin/find "$dir" -type f 2>/dev/null | grep -c . || true)
if ! sudo /usr/bin/find "$dir" -type f -delete; then
    echo "Removing core dumps failed"
    exit 1
fi
after=$(du -sm "$dir" 2>/dev/null | awk '{print $1}')
echo "Removed ${count:-0} files, freed $(( ${before:-0} - ${after:-0} )) MB"
