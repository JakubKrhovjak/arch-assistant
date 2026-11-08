"""The uninstall guard: the real removal set (pacman -Rs --print) checked against the protected list, the login
shell and what Arch Assistant needs, in the window (protection.py) and again in actions/uninstall.sh (_lib.sh)."""
import logging
import subprocess
from pathlib import Path

import pytest

from arch_assistant import protection as P
from arch_assistant.actions import TERMINAL, run_action
from arch_assistant.model import Action

REPO = Path(__file__).resolve().parent.parent
ACTIONS = REPO / "actions"
LOG = logging.getLogger("test")


def test_lists_have_one_home():
    assert "linux-*" in P.PATTERNS and "plasma" in P.GROUPS and not any(p.startswith("@") for p in P.PATTERNS)
    assert {"pyside6", "python-jeepney", "pacman-contrib", "arch-audit"} <= set(P.DEPENDENCIES)
    # install.sh installs and marks as explicit exactly what the guard protects
    out = subprocess.run(["bash", str(REPO / "install.sh"), "--dry-run"], capture_output=True, text=True, timeout=30,
                         check=False).stdout
    assert f"mark {' '.join(P.DEPENDENCIES)} as explicitly installed" in out


def test_the_action_terminal_is_protected():
    # unused-apps must never offer to uninstall the terminal the long actions run in
    assert TERMINAL in P.DEPENDENCIES


def test_bash_and_python_read_a_list_file_the_same(tmp_path):
    sample = tmp_path / "list.txt"
    sample.write_text("# a header comment\n\nlinux-*\n  @plasma   # a group, inline comment\n\t\nzsh\n#gone\nvlc#tail\n")
    out = subprocess.run(["bash", "-c", f'source "{ACTIONS}/_lists.sh" && list_file "$1"', "bash", str(sample)],
                         capture_output=True, text=True, timeout=10, check=True).stdout
    assert tuple(out.splitlines()) == P.read_list(sample) == ("linux-*", "@plasma", "zsh", "vlc")


def test_protection_reasons():
    reasons = P.protection_reasons(["linux-zen", "bluedevil", "zsh", "pyside6", "vlc"], members={"bluedevil"},
                                   shells={"zsh", "bash"})
    assert reasons == {"linux-zen": P.REASON_SYSTEM, "bluedevil": P.REASON_SYSTEM, "zsh": P.REASON_SHELL,
                       "pyside6": P.REASON_SELF}


ZSH_PLUGINS = ("zsh-autosuggestions", "zsh-completions", "zsh-history-substring-search", "zsh-syntax-highlighting")


def test_removal_plan_blocks_the_login_shell_taken_as_a_dependency(shim):
    shim.pacman_removal("\n".join([*reversed(ZSH_PLUGINS), "zsh"]) + "\n")
    plan = P.removal_plan(f"repo:{p}" for p in ZSH_PLUGINS)
    assert plan.dependencies == ("zsh",) and plan.blocked == (("zsh", "login shell"),)
    assert plan.problem == "blocked: zsh (login shell)"
    assert "pacman -Rs --print --print-format %n " + " ".join(ZSH_PLUGINS) in shim.calls()


def test_removal_plan_blocks_what_arch_assistant_needs(shim):
    shim.pacman_removal("torbrowser-launcher\ntor\npyside6\nshiboken6\n")
    plan = P.removal_plan(["aur:torbrowser-launcher"])
    assert plan.problem == "blocked: pyside6 (needed by Arch Assistant)"


def test_removal_plan_lists_dependencies_and_lets_a_clean_removal_through(shim):
    shim.pacman_removal("vlc\nlibvlc\nlua52\n")
    plan = P.removal_plan(["repo:vlc", "flatpak:org.x"])
    assert plan.packages == ("vlc", "libvlc", "lua52") and plan.dependencies == ("libvlc", "lua52")
    assert plan.flatpaks == ("org.x",) and plan.problem == ""


def test_removal_plan_fails_closed_when_pacman_refuses(shim):
    shim.cmd("pacman", exit_code=1, stderr="error: target not found: nope\n")
    plan = P.removal_plan(["repo:nope"])
    assert plan.problem == "error: target not found: nope" and plan.packages == ()


def test_removal_plan_of_flatpaks_only_needs_no_pacman(shim):
    shim.cmd("pacman", exit_code=1)
    assert P.removal_plan(["flatpak:org.x"]).problem == "" and shim.calls() == []


# --- the same guard in actions/uninstall.sh ------------------------------------------------------

GUARD_CASES = [
    (ZSH_PLUGINS, "\n".join([*ZSH_PLUGINS, "zsh"]) + "\n"),
    (("torbrowser-launcher",), "torbrowser-launcher\ntor\npyside6\nshiboken6\n"),
    (("kwin-addon",), "kwin-addon\nkwin\n"),                       # kwin: a member of the protected group plasma
    (("linux-zen",), "linux-zen\n"),                                # picked directly (a pattern)
]


@pytest.mark.parametrize(("picked", "targets"), GUARD_CASES)
def test_uninstall_action_refuses_the_same_removal_as_the_window(shim, picked, targets):
    shim.sudo()
    shim.pacman_removal(targets)
    expected = P.removal_plan(f"repo:{p}" for p in picked).problem
    assert expected.startswith("blocked: ")
    shim.calls_file.unlink()
    r = run_action(ACTIONS, Action("uninstall", "Uninstall", tuple(f"repo:{p}" for p in picked)), LOG)
    assert not r.ok and r.output == f"Uninstall failed: {expected}"
    assert not any(c.startswith("sudo") or "-Rns" in c for c in shim.calls())   # nothing was removed


def test_uninstall_action_reports_a_removal_pacman_cannot_resolve(shim):
    shim.sudo()
    shim.cmd("pacman", exit_code=1, stderr="error: target not found: nope\n")
    r = run_action(ACTIONS, Action("uninstall", "Uninstall", ("repo:nope",)), LOG)
    assert not r.ok and r.output == "Uninstall failed: error: target not found: nope"
    assert not any(c.startswith("sudo") for c in shim.calls())
