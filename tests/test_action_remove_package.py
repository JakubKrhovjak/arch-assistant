import logging
from pathlib import Path

from arch_assistant.actions import run_action
from arch_assistant.model import Action

ACTIONS = Path(__file__).resolve().parent.parent / "actions"
LOG = logging.getLogger("test")
SUDO = 'cmd=$(basename "$1"); shift; exec "$cmd" "$@"\n'


def test_remove_package(shim):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("pacman", "")
    r = run_action(ACTIONS, Action("remove-package", "Remove package", ("vlc",)), LOG)
    assert r.ok and r.output == "Removed package vlc"
    assert shim.calls()[0] == "sudo /usr/bin/pacman -Rns --noconfirm vlc"


def test_remove_package_failure(shim):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("pacman", "", exit_code=1, stderr="error: failed to prepare transaction (could not satisfy dependencies)")
    r = run_action(ACTIONS, Action("remove-package", "Remove package", ("x",)), LOG)
    assert not r.ok and r.output.startswith("Removing x failed: error: failed to prepare")
