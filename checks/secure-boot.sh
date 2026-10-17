#!/bin/bash
# Check: after a kernel/system update, the UKI in the ESP exists and is signed (sbctl verify).
# Reports a critical finding on any unsigned/missing image; reports "OK" once per update run.
# Env: AA_ESP (default: bootctl -p / /boot/efi), AA_STATE_DIR (state.json -> last_update_run).
set -uo pipefail

esp="${AA_ESP:-$(bootctl -p 2>/dev/null || echo /boot/efi)}"
state_dir="${AA_STATE_DIR:-.}"
verify=$(sudo /usr/bin/sbctl verify 2>&1)
verify_rc=$?
# the ESP is usually mounted root-only, so list it through sudo
ukis=$(sudo /usr/bin/find "$esp/EFI/Linux" -maxdepth 1 -name '*.efi' 2>/dev/null || true)

ESP="$esp" STATE_DIR="$state_dir" VERIFY="$verify" VERIFY_RC="$verify_rc" UKIS="$ukis" python3 - <<'PY'
import json, os
from pathlib import Path

esp, state_dir = os.environ["ESP"], Path(os.environ["STATE_DIR"])
verify, ukis = os.environ["VERIFY"], [u for u in os.environ["UKIS"].splitlines() if u]
unsigned = [l.split()[1] for l in verify.splitlines() if l.strip().startswith("✗") and "is not signed" in l]
signed = [l for l in verify.splitlines() if l.strip().startswith("✓")]
fix = "Fix manually: sbctl sign -s <file>, see custom-arch-setup-script/pc/secureboot-tpm2.sh"

def out(**kw):
    print(json.dumps({"id": "secure-boot", "actions": [], **kw}))

# the guard fails closed: a verify that did not run or found nothing signed is treated as broken
if os.environ["VERIFY_RC"] != "0" or (not signed and not unsigned):
    first = (verify.strip().splitlines() or ["no output"])[-1][:160]
    out(severity="critical", title="sbctl verify failed, Secure Boot state unknown",
        summary=f"sbctl verify exited with {os.environ['VERIFY_RC']}: {first}. {fix}", items=[{"name": "sbctl verify"}])
    raise SystemExit
if not ukis:
    out(severity="critical", title=f"No UKI found in {esp}/EFI/Linux", summary=f"The system will not boot. {fix}",
        items=[{"name": f"{esp}/EFI/Linux"}])
    raise SystemExit
if unsigned:
    out(severity="critical", title=f"UKI is not signed: {unsigned[0]}",
        summary=f"{len(unsigned)} unsigned image(s): {', '.join(unsigned)}. Secure Boot will refuse to boot them. {fix}",
        items=[{"name": u} for u in unsigned])
    raise SystemExit

try:
    last_update = json.loads((state_dir / "state.json").read_text()).get("last_update_run")
except Exception:
    last_update = None
marker = state_dir / "secure-boot.json"
try:
    reported = json.loads(marker.read_text()).get("reported_update_run")
except Exception:
    reported = None
if last_update and last_update != reported:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"reported_update_run": last_update}))
    out(severity="low", title="Secure Boot OK after update",
        summary=f"{len(signed)} signed EFI images, {len(ukis)} UKI(s) in {esp}/EFI/Linux", items=[])
else:
    out(severity="none", title="Secure Boot OK", summary="")
PY
