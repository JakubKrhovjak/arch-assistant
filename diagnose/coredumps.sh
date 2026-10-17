#!/bin/bash
# Diagnose collector: recent core dumps and details of the latest one.
set -uo pipefail
cat >/dev/null
echo "===== recent core dumps"
coredumpctl list --no-pager --since -7days 2>/dev/null | tail -15
echo "===== latest dump info"
coredumpctl info --no-pager 2>/dev/null | head -40 | cut -c1-240
