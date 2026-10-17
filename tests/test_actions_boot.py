import logging
from pathlib import Path

from arch_assistant.actions import run_action
from arch_assistant.model import Action

ACTIONS = Path(__file__).resolve().parent.parent / "actions"
LOG = logging.getLogger("test")
SUDO = 'cmd=$(basename "$1"); shift; exec "$cmd" "$@"\n'


def test_refresh_mirrors(shim, tmp_path):
    ml = tmp_path / "mirrorlist"
    shim.cmd("sudo", script=SUDO)
    shim.cmd("reflector", script=f'printf "Server = a\\nServer = b\\n# c\\n" > {ml}\n')
    import os
    os.environ["AA_MIRRORLIST"] = str(ml)
    try:
        r = run_action(ACTIONS, Action("refresh-mirrors", "Refresh mirrors"), LOG)
    finally:
        del os.environ["AA_MIRRORLIST"]
    assert r.ok and r.output == "Mirrorlist refreshed (2 servers)"
    call = [c for c in shim.calls() if c.startswith("sudo /usr/bin/reflector")][0]
    assert "--country Czechia,Germany" in call and "--protocol https" in call and "--latest 20" in call
    assert f"--save {ml}" in call


def test_refresh_mirrors_failure(shim):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("reflector", "", exit_code=1, stderr="error: no mirrors")
    r = run_action(ACTIONS, Action("refresh-mirrors", "Refresh mirrors"), LOG)
    assert not r.ok and r.output == "Mirrorlist refresh failed: error: no mirrors"


def test_pacdiff_opens_terminal(shim):
    shim.cmd("ghostty", "")
    r = run_action(ACTIONS, Action("pacdiff", "Review .pacnew files"), LOG)
    assert r.ok and r.output == "Opened pacdiff in a terminal"
    assert shim.calls()[0] == "ghostty -e sudo /usr/bin/pacdiff"
