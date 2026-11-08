"""Actions run visibly in a terminal window: same result contract as the background run."""
import logging
import stat

from arch_assistant import actions as A
from arch_assistant.config import load_config
from arch_assistant.model import Action

LOG = logging.getLogger("test")

# A stand-in for Ghostty: skips its own options, runs the command after -e and answers the
# "Press Enter to close" prompt; `kill` makes it die mid-way like a closed window.
FAKE_TERMINAL = """\
while [ "$1" != "-e" ]; do shift; done; shift
echo | "$@"
"""


def _action(d, name, body):
    p = d / f"{name}.sh"
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def test_terminal_run_keeps_result_contract(tmp_path, shim):
    shim.cmd("ghostty", script=FAKE_TERMINAL)
    _action(tmp_path, "update", 'echo "Updated 3 packages"\necho "pacman output" >&2\n')
    r = A.run_action_in_terminal(tmp_path, Action("update", "Update system"), LOG, poll_s=0.05)
    assert r.ok and r.output == "Updated 3 packages" and r.detail == "pacman output"
    assert any(c.startswith("ghostty --title=Arch Assistant: Update system -e bash -c") for c in shim.calls())


def test_terminal_run_reports_failure_and_args(tmp_path, shim):
    shim.cmd("ghostty", script=FAKE_TERMINAL)
    _action(tmp_path, "remove-package", 'echo "Removal of $1 failed"\necho "error: target" >&2\nexit 1\n')
    r = A.run_action_in_terminal(tmp_path, Action("remove-package", "Remove package", ("vlc",)), LOG, poll_s=0.05)
    assert not r.ok and r.output == "Removal of vlc failed" and "error: target" in r.detail


def test_closed_window_is_a_failure(tmp_path, shim):
    # the wrapper shell dies before writing the exit code, as when the window is closed
    shim.cmd("ghostty", script='while [ "$1" != "-e" ]; do shift; done; shift\n'
                               '"$@" &\nsleep 0.3\npkill -P $$ -f "sleep 30"; kill %1\n')
    _action(tmp_path, "update", "sleep 30\n")
    r = A.run_action_in_terminal(tmp_path, Action("update", "Update system"), LOG, poll_s=0.05)
    assert not r.ok and "terminal window was closed" in r.output


def test_terminal_that_never_starts_is_a_failure(tmp_path, shim):
    shim.cmd("ghostty", script="exit 0\n")
    _action(tmp_path, "update", 'echo never\n')
    r = A.run_action_in_terminal(tmp_path, Action("update", "Update system"), LOG, poll_s=0.05, start_grace_s=0.3)
    assert not r.ok and "did not start" in r.output


def test_missing_script(tmp_path):
    r = A.run_action_in_terminal(tmp_path, Action("nope", "Nope"), LOG)
    assert not r.ok and "not found" in r.output


def test_terminal_available_needs_terminal_and_display(tmp_path, shim):
    shim.cmd("ghostty")
    path = str(shim.bin_dir)
    assert A.terminal_available({"PATH": path, "WAYLAND_DISPLAY": "wayland-0"})
    assert not A.terminal_available({"PATH": path})
    assert not A.terminal_available({"PATH": str(tmp_path / "empty"), "DISPLAY": ":0"})


def test_terminal_progress_config(tmp_path):
    assert load_config(tmp_path, tmp_path).terminal_progress is True
    (tmp_path / "config.toml").write_text("terminal_progress = false\n")
    assert load_config(tmp_path, tmp_path).terminal_progress is False


def test_long_actions_use_the_terminal():
    assert {"update", "clean-pkg-cache", "remove-orphans"} <= A.TERMINAL_ACTIONS
    assert "restart-unit" not in A.TERMINAL_ACTIONS
