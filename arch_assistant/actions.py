"""Running action scripts (actions/<id>.sh) and the rules that force a confirmation."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable

from .config import Config, Mode
from .model import Action, ActionResult, Finding

TIMEOUTS: dict[str, float] = {"update": 1800, "uninstall": 900, "install": 1800}
DEFAULT_TIMEOUT = 300
# Checks the tray runs right after an action finishes (regardless of its result).
POST_HOOKS: dict[str, tuple[str, ...]] = {"update": ("secure-boot", "reboot-needed")}
# Actions that may never run without a click, whatever the configured mode says. The system update only
# runs from the Updates tab, uninstalling from the Installed tab and installing from the Browse tab, where the
# packages are reviewed and picked.
NEVER_AUTO = {"remove-package", "remove-driver-packages", "pacdiff", "update", "uninstall", "install"}
VM_SENSITIVE = ("qemu*", "libvirt*")


def _command(actions_dir: Path, action: Action) -> list[str] | None:
    """The command line of actions/<id>.sh or .py with the action's args; None when there is no script."""
    script = next((p for p in (actions_dir / f"{action.id}.sh", actions_dir / f"{action.id}.py") if p.is_file()), None)
    if script is None:
        return None
    cmd = [sys.executable, str(script)] if script.suffix == ".py" else [str(script)]
    return [*cmd, *action.args]


def _result(action: Action, returncode: int, stdout: str, stderr: str, start: float,
            log: logging.Logger) -> ActionResult:
    """stdout = short result (first 3 lines go to the popup), the rest and stderr = detail (log)."""
    lines = stdout.strip().splitlines()
    output = "\n".join(lines[:3])
    detail = "\n".join([*lines[3:], stderr.strip()]).strip()
    ok = returncode == 0
    log.log(logging.INFO if ok else logging.ERROR, "action %s %s: %s\n%s", action.id,
            "ok" if ok else f"failed (exit {returncode})", output, detail)
    return ActionResult(action.id, ok, output, detail, time.monotonic() - start)


def run_action(actions_dir: Path, action: Action, log: logging.Logger) -> ActionResult:
    """Run actions/<id>.sh with the action's args in the background. stdout = short result, stderr = detail."""
    cmd = _command(actions_dir, action)
    timeout = TIMEOUTS.get(action.id, DEFAULT_TIMEOUT)
    start = time.monotonic()
    if cmd is None:
        return ActionResult(action.id, False, f"Action script {action.id}.sh not found", "", 0.0)
    log.info("action %s start args=%s", action.id, list(action.args))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=os.environ.copy(), check=False)
    except subprocess.TimeoutExpired:
        msg = f"Action {action.id} exceeded {timeout / 60:g} min"
        log.error(msg)
        return ActionResult(action.id, False, msg, "", time.monotonic() - start)
    except OSError as e:
        log.error("action %s cannot start: %s", action.id, e)
        return ActionResult(action.id, False, f"Action {action.id} cannot start: {e}", "", time.monotonic() - start)
    return _result(action, proc.returncode, proc.stdout, proc.stderr, start, log)


# Actions that take long enough to be worth watching: a click runs them in a terminal window.
TERMINAL_ACTIONS = frozenset({"update", "refresh-mirrors", "clean-pkg-cache", "remove-orphans",
                              "remove-package", "remove-driver-packages", "uninstall", "install"})
TERMINAL = "ghostty"

# Runs inside the terminal: the action's stderr (pacman/yay output) is shown live and saved, stdout
# (the short result) is saved and shown at the end; the exit code is written last, atomically, and is
# what the tray waits for. The window stays open until Enter, so the output can be read.
TERMINAL_WRAPPER = r'''
out=$1 err=$2 rc=$3 pid=$4; shift 4
echo $$ > "$pid"
{ "$@" 2>&1 1>&3 3>&- | tee "$err"; } 3>"$out"
status=${PIPESTATUS[0]}
echo
cat "$out"
echo "$status" > "$rc.tmp" && mv "$rc.tmp" "$rc"
echo
if [ "$status" -eq 0 ]; then echo "Done."; else echo "Failed (exit $status)."; fi
read -rp "Press Enter to close..." _
'''


def terminal_available(env: dict[str, str] | None = None) -> bool:
    """A terminal window can be opened: the terminal is installed and there is a graphical session."""
    env = os.environ if env is None else env
    return shutil.which(TERMINAL, path=env.get("PATH")) is not None and bool(env.get("WAYLAND_DISPLAY") or env.get("DISPLAY"))


def _alive(pid_file: Path) -> bool:
    """The wrapper shell is still running (or has not started yet)."""
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_action_in_terminal(actions_dir: Path, action: Action, log: logging.Logger,
                           poll_s: float = 0.5, start_grace_s: float = 30) -> ActionResult:
    """Run the action like run_action, but visibly in a terminal window; same result contract.

    The terminal may hand the window to an already running instance and exit at once, so the tray
    waits for the exit-code file, not for the terminal process. Closing the window early (the wrapper
    shell dies without an exit code) or a terminal that never starts the wrapper counts as a failure.
    """
    cmd = _command(actions_dir, action)
    timeout = TIMEOUTS.get(action.id, DEFAULT_TIMEOUT)
    start = time.monotonic()
    if cmd is None:
        return ActionResult(action.id, False, f"Action script {action.id}.sh not found", "", 0.0)
    log.info("action %s start in terminal args=%s", action.id, list(action.args))
    with tempfile.TemporaryDirectory(prefix="arch-assistant-") as tmp:
        out, err, rc, pid = (Path(tmp) / n for n in ("out", "err", "rc", "pid"))
        argv = [TERMINAL, f"--title=Arch Assistant: {action.label}", "-e", "bash", "-c", TERMINAL_WRAPPER,
                "arch-assistant", str(out), str(err), str(rc), str(pid), *cmd]
        try:
            subprocess.Popen(argv, env=os.environ.copy(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            log.error("action %s: terminal cannot start: %s", action.id, e)
            return ActionResult(action.id, False, f"Terminal for {action.label} cannot start: {e}", "",
                                time.monotonic() - start)
        while not rc.exists():
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                msg = f"Action {action.id} exceeded {timeout / 60:g} min"
                log.error(msg)
                return ActionResult(action.id, False, msg, "", elapsed)
            if not pid.exists() and elapsed > start_grace_s:
                msg = f"Terminal for {action.label} did not start"
                log.error("action %s: %s", action.id, msg)
                return ActionResult(action.id, False, msg, "", elapsed)
            if pid.exists() and not _alive(pid) and not rc.exists():
                msg = f"{action.label} was stopped: the terminal window was closed"
                log.error("action %s: %s", action.id, msg)
                return ActionResult(action.id, False, msg, _read(err), elapsed)
            time.sleep(poll_s)
        try:
            code = int(rc.read_text().strip())
        except ValueError:
            code = 1
        return _result(action, code, _read(out), _read(err), start, log)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def running_vms() -> list[str] | None:
    """Names of running libvirt domains; None when the state is unknown (virsh missing or failing)."""
    try:
        proc = subprocess.run(["virsh", "-c", "qemu:///system", "list", "--name", "--state-running"],
                              capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return [l.strip() for l in proc.stdout.splitlines() if l.strip()]


def needs_confirmation(action: Action, finding: Finding, findings: list[Finding],
                       running_vms: Callable[[], list[str] | None] = running_vms) -> str | None:
    """Reason why an `auto` action must ask anyway, or None. Only `update` has such rules.

    The guards fail closed: when the Arch news check is missing or failed, or the VM state is
    unknown, an automatic update is not allowed to proceed.
    """
    if action.id != "update":
        return None
    names = [str(i.get("name", "")) for i in finding.items]
    news = next((n for n in findings if n.id == "arch-news"), None)
    if news is None or news.error:
        return "Arch news check unavailable, cannot verify manual-intervention notices"
    affected = sorted({p for i in news.items for p in i.get("packages", []) if p in names})
    if affected:
        return f"Arch news reports manual intervention for: {', '.join(affected)}"
    sensitive = [n for n in names if any(fnmatch(n, p) for p in VM_SENSITIVE)]
    if sensitive:
        vms = running_vms()
        if vms is None:
            return f"{', '.join(sensitive)} would be updated but the VM state is unknown"
        if vms:
            return f"{', '.join(sensitive)} would be updated while VM {', '.join(vms)} is running"
    return None


def effective_mode(action_id: str, cfg: Config, args: tuple[str, ...] = ()) -> Mode:
    """Configured mode, capped at `ask` for actions that must never run unattended."""
    mode = cfg.actions.get(action_id, "ask")
    never_auto = action_id in NEVER_AUTO or (action_id == "restart-unit" and args[:1] == ("system",))
    if mode == "auto" and never_auto:
        return "ask"
    return mode
