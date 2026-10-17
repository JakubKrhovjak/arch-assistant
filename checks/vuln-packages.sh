#!/bin/bash
# Check: installed packages with known vulnerabilities (arch-audit); high when a fixed version is in the repos.
set -uo pipefail

if ! command -v arch-audit >/dev/null 2>&1; then
    echo "arch-audit is not installed" >&2
    exit 1
fi
report=$(arch-audit --format "%n %c %s" 2>/dev/null || true)
fixable=$(arch-audit -u --format "%n" 2>/dev/null || true)

REPORT="$report" FIXABLE="$fixable" python3 - <<'PY'
import json, os, re
fixable = {l.strip() for l in os.environ["FIXABLE"].splitlines() if l.strip()}
items = []
for line in os.environ["REPORT"].splitlines():
    parts = line.split()
    if not parts:
        continue
    name = parts[0]
    cves = [p.rstrip(",") for p in parts[1:] if p.startswith("CVE-")]
    risk = " ".join(p for p in parts[1:] if not p.startswith("CVE-")).strip() or "unknown"
    items.append({"name": name, "cves": cves, "risk": risk, "fix_available": name in fixable})
if not items:
    print(json.dumps({"id": "vuln-packages", "severity": "none", "title": "No known vulnerable packages", "summary": ""}))
    raise SystemExit
n_fix = sum(1 for i in items if i["fix_available"])
n = len(items)
print(json.dumps({
    "id": "vuln-packages", "severity": "high" if n_fix else "medium",
    "title": f"{n} package{'s' if n != 1 else ''} with known vulnerabilities ({n_fix} fixable by update)",
    "summary": ", ".join(f"{i['name']} ({len(i['cves'])} CVE, {i['risk']}{', fix available' if i['fix_available'] else ''})" for i in items),
    "items": items, "actions": [],
}))
PY
