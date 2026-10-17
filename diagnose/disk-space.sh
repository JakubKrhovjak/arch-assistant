#!/bin/bash
# Diagnose collector: filesystem usage and the biggest directories in the home directory (bounded).
set -uo pipefail
cat >/dev/null
echo "===== df"
df -h -x tmpfs -x devtmpfs 2>/dev/null
echo "===== biggest entries in \$HOME (1 level)"
timeout 30 du -xh -d1 "$HOME" 2>/dev/null | sort -rh | head -15
echo "===== biggest entries in /var (1 level)"
timeout 20 du -xh -d1 /var 2>/dev/null | sort -rh | head -8
