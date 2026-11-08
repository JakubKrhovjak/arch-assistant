import logging
from pathlib import Path

from arch_assistant.actions import run_action
from arch_assistant.model import Action

ACTIONS = Path(__file__).resolve().parent.parent / "actions"
LOG = logging.getLogger("test")


def _systemctl(load: str, active: str, sub: str) -> str:
    return f'printf "LoadState={load}\\nActiveState={active}\\nSubState={sub}\\n"\n'


def test_localsend_running_is_quiet(shim, check_runner):
    shim.cmd("systemctl", script=_systemctl("loaded", "active", "running"))
    f = check_runner.run("localsend")
    assert f.severity == "none" and f.title == "LocalSend is running"
    assert shim.calls() == ["systemctl --user show localsend.service -p LoadState -p ActiveState -p SubState"]


def test_localsend_not_installed_is_quiet(shim, check_runner):
    shim.cmd("systemctl", script=_systemctl("not-found", "inactive", "dead"))
    assert check_runner.run("localsend").severity == "none"


def test_localsend_stopped_offers_restart(shim, check_runner):
    shim.cmd("systemctl", script=_systemctl("loaded", "failed", "failed"))
    f = check_runner.run("localsend")
    assert f.severity == "medium" and f.title == "LocalSend is not running"
    assert "failed" in f.summary
    assert [(a.id, a.args) for a in f.actions] == [("restart-localsend", ())]


def test_restart_localsend_clears_failed_state_first(shim):
    shim.cmd("systemctl", "")
    r = run_action(ACTIONS, Action("restart-localsend", "Restart LocalSend", ()), LOG)
    assert r.ok and r.output == "LocalSend restarted"
    assert shim.calls() == ["systemctl --user reset-failed localsend.service",
                            "systemctl --user restart localsend.service"]


def test_restart_localsend_failure(shim):
    shim.cmd("systemctl", script='[ "$2" = restart ] && { echo "Unit localsend.service not found." >&2; exit 5; }\nexit 0\n')
    r = run_action(ACTIONS, Action("restart-localsend", "Restart LocalSend", ()), LOG)
    assert not r.ok and r.output == "Restart of LocalSend failed: Unit localsend.service not found."
