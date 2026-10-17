"""Running action scripts (actions/<id>.sh) and the rules that force a confirmation."""
from __future__ import annotations

import logging
import os
import subprocess
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable

from .config import Config, Mode
from .model import Action, ActionResult, Finding

TIMEOUTS: dict[str, float] = {"update": 1800}
DEFAULT_TIMEOUT = 300
# Checks the tray runs right after an action finishes (regardless of its result).
POST_HOOKS: dict[str, tuple[str, ...]] = {"update": ("secure-boot", "reboot-needed")}
# Actions that may never run without a click, whatever the configured mode says.
NEVER_AUTO = {"remove-package", "pacdiff"}
VM_SENSITIVE = ("qemu*", "libvirt*")


def run_action(actions_dir: Path, action: Action, log: logging.Logger) -> ActionResult:
    """Run actions/<id>.sh with the action's args. stdout = short result (3 lines), stderr = detail."""
    script = actions_dir / f"{action.id}.sh"
    timeout = TIMEOUTS.get(action.id, DEFAULT_TIMEOUT)
    start = time.monotonic()
    if not script.is_file():
        return ActionResult(action.id, False, f"Action script {script.name} not found", "", 0.0)
    log.info("action %s start args=%s", action.id, list(action.args))
    try:
        proc = subprocess.run([str(script), *action.args], capture_output=True, text=True,
                              timeout=timeout, env=os.environ.copy(), check=False)
    except subprocess.TimeoutExpired:
        msg = f"Action {action.id} exceeded {timeout / 60:g} min"
        log.error(msg)
        return ActionResult(action.id, False, msg, "", time.monotonic() - start)
    lines = proc.stdout.strip().splitlines()
    output = "\n".join(lines[:3])
    detail = "\n".join([*lines[3:], proc.stderr.strip()]).strip()
    ok = proc.returncode == 0
    log.log(logging.INFO if ok else logging.ERROR, "action %s %s: %s\n%s", action.id,
            "ok" if ok else f"failed (exit {proc.returncode})", output, detail)
    return ActionResult(action.id, ok, output, detail, time.monotonic() - start)


def running_vms() -> list[str]:
    try:
        proc = subprocess.run(["virsh", "-c", "qemu:///system", "list", "--name", "--state-running"],
                              capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [l.strip() for l in proc.stdout.splitlines() if l.strip()] if proc.returncode == 0 else []


def needs_confirmation(action: Action, finding: Finding, findings: list[Finding],
                       running_vms: Callable[[], list[str]] = running_vms) -> str | None:
    """Reason why an `auto` action must ask anyway, or None. Only `update` has such rules."""
    if action.id != "update":
        return None
    names = [str(i.get("name", "")) for i in finding.items]
    affected = sorted({p for n in findings if n.id == "arch-news" for i in n.items
                       for p in i.get("packages", []) if p in names})
    if affected:
        return f"Arch news reports manual intervention for: {', '.join(affected)}"
    sensitive = [n for n in names if any(fnmatch(n, p) for p in VM_SENSITIVE)]
    if sensitive:
        vms = running_vms()
        if vms:
            return f"{', '.join(sensitive)} would be updated while VM {', '.join(vms)} is running"
    return None


def effective_mode(action_id: str, cfg: Config, args: tuple[str, ...] = ()) -> Mode:
    mode = cfg.actions.get(action_id, "ask")
    if mode == "auto" and action_id in NEVER_AUTO:
        return "ask"
    return mode
