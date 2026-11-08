import logging
from pathlib import Path

from arch_assistant.actions import run_action
from arch_assistant.model import Action

ACTIONS = Path(__file__).resolve().parent.parent / "actions"
LOG = logging.getLogger("test")

PACMAN_OK = '''
if [[ "$1" == "-Qu" ]]; then printf 'a 1 -> 2\\nb 1 -> 2\\nc 1 -> 2\\n'; exit 0; fi
if [[ "$1" == "-Syu" ]]; then echo ":: Running post-transaction hooks..."; exit 0; fi
exit 0
'''

PACMAN_FAIL = '''
if [[ "$1" == "-Qu" ]]; then printf 'a 1 -> 2\\n'; exit 0; fi
if [[ "$1" == "-Syu" ]]; then echo "error: failed to commit transaction (conflicting files)" >&2; exit 1; fi
exit 0
'''


def _setup(shim, pacman_body, yay_list="", pacman_log="", flatpak_rc=0):
    shim.cmd("flatpak", script=(
        'if [[ "$1" == "remote-ls" ]]; then printf "com.google.Chrome\\t1\\norg.x\\t2\\n"; exit 0; fi\n'
        + ('echo "error: Flatpak broke" >&2; exit 1\n' if flatpak_rc else 'echo "Updating..."; exit 0\n')))
    # sudo shim: strip the absolute path so the pacman shim on PATH is used
    shim.cmd("sudo", script='cmd=$(basename "$1"); shift; exec "$cmd" "$@"\n')
    shim.cmd("pacman", script=pacman_body)
    shim.cmd("yay", script=f'if [[ "$1" == "-Qua" ]]; then printf %s {_q(yay_list)}; exit 0; fi\necho " there is nothing to do"\nexit 0\n')
    log = shim.bin_dir / "pacman.log"
    log.write_text(pacman_log)
    return {"AA_PACMAN_LOG": str(log)}


def _q(s):
    return "'" + s + "'"


def test_update_success_counts_packages(shim, monkeypatch):
    env = _setup(shim, PACMAN_OK, yay_list="aur/x 1-1 -> 1-2\n")
    monkeypatch.setenv("AA_PACMAN_LOG", env["AA_PACMAN_LOG"])
    r = run_action(ACTIONS, Action("update", "Update system"), LOG)
    assert r.ok, r.detail
    assert r.output.splitlines()[0] == "Updated 3 packages (repo), 1 (AUR) and 2 Flatpaks"
    assert any(c.startswith("sudo /usr/bin/flatpak update -y --noninteractive") for c in shim.calls())


def test_update_flatpak_failure_is_reported(shim, monkeypatch):
    env = _setup(shim, PACMAN_OK, flatpak_rc=1)
    monkeypatch.setenv("AA_PACMAN_LOG", env["AA_PACMAN_LOG"])
    r = run_action(ACTIONS, Action("update", "Update system"), LOG)
    assert not r.ok
    assert r.output.startswith("Updated 3 packages (repo), 0 (AUR); Flatpak update failed: error: Flatpak broke")
    assert any(c.startswith("sudo /usr/bin/pacman -Syu --noconfirm") for c in shim.calls())
    assert any(c.startswith("yay -Sua --noconfirm") for c in shim.calls())


def test_update_reports_pacnew_files(shim, monkeypatch):
    env = _setup(shim, PACMAN_OK, pacman_log="[2026-10-17T10:00:00+0200] [ALPM] warning: /etc/pacman.conf installed as /etc/pacman.conf.pacnew\n")
    monkeypatch.setenv("AA_PACMAN_LOG", env["AA_PACMAN_LOG"])
    monkeypatch.setenv("AA_UPDATE_SINCE", "2026-10-17T09:00:00")
    r = run_action(ACTIONS, Action("update", "Update system"), LOG)
    assert r.ok
    assert r.output.splitlines()[1] == "New .pacnew files: 1"


def test_update_failure_first_error_line(shim, monkeypatch):
    env = _setup(shim, PACMAN_FAIL)
    monkeypatch.setenv("AA_PACMAN_LOG", env["AA_PACMAN_LOG"])
    r = run_action(ACTIONS, Action("update", "Update system"), LOG)
    assert not r.ok
    assert r.output.startswith("Update failed: error: failed to commit transaction")
    assert not any(c.startswith("yay -Sua") for c in shim.calls())


# --- selective update (args from the Updates window) ---------------------------------------------

def _run(shim, monkeypatch, *args, yay_list=""):
    env = _setup(shim, PACMAN_OK, yay_list=yay_list)
    monkeypatch.setenv("AA_PACMAN_LOG", env["AA_PACMAN_LOG"])
    return run_action(ACTIONS, Action("update", "Update system", tuple(args)), LOG)


def test_update_selected_repo_aur_and_flatpak(shim, monkeypatch):
    r = _run(shim, monkeypatch, "repo", "aur:mbpoll-git", "aur:yay-bin", "flatpak:org.x")
    assert r.ok, r.detail
    assert r.output.splitlines()[0] == "Updated 3 packages (repo), 2 (AUR) and 1 Flatpaks"
    calls = shim.calls()
    assert "sudo /usr/bin/pacman -Syu --noconfirm" in calls
    assert "yay -S --aur --noconfirm mbpoll-git yay-bin" in calls
    assert "sudo /usr/bin/flatpak update -y --noninteractive org.x" in calls
    assert not any(c.startswith(("yay -Sua", "yay -Qua", "flatpak remote-ls")) for c in calls)


def test_update_selected_aur_only_skips_pacman_and_flatpak(shim, monkeypatch):
    r = _run(shim, monkeypatch, "aur:mbpoll-git", yay_list="aur/mbpoll-git 1-1 -> 1-2\naur/other 1 -> 2\n")
    assert r.ok, r.detail
    assert r.output.splitlines()[0] == "Updated 0 packages (repo), 1 (AUR) and 0 Flatpaks"
    calls = shim.calls()
    assert calls == ["yay -S --aur --noconfirm mbpoll-git"]


def test_update_selected_flatpak_only(shim, monkeypatch):
    r = _run(shim, monkeypatch, "flatpak:com.google.Chrome", "flatpak:org.x")
    assert r.ok, r.detail
    assert r.output.splitlines()[0] == "Updated 0 packages (repo), 0 (AUR) and 2 Flatpaks"
    assert shim.calls() == ["sudo /usr/bin/flatpak update -y --noninteractive com.google.Chrome org.x",
                            "flatpak update -y --noninteractive com.google.Chrome org.x"]


def test_update_selected_repo_only_leaves_aur_and_flatpak(shim, monkeypatch):
    r = _run(shim, monkeypatch, "repo", yay_list="aur/x 1 -> 2\n")
    assert r.ok, r.detail
    assert r.output.splitlines()[0] == "Updated 3 packages (repo), 0 (AUR) and 0 Flatpaks"
    assert not any(c.startswith(("yay", "sudo /usr/bin/flatpak")) for c in shim.calls())


def test_update_rejects_bad_arguments_before_running_anything(shim, monkeypatch):
    for bad in ("aur:--noconfirm", "flatpak:", "everything", "aur:a b"):
        shim.calls_file.unlink(missing_ok=True)
        r = _run(shim, monkeypatch, bad)
        assert not r.ok and r.output.startswith("Update failed:"), bad
        assert shim.calls() == [], bad
