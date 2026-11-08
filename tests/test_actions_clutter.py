import logging
from pathlib import Path

from arch_assistant.actions import run_action
from arch_assistant.model import Action

ACTIONS = Path(__file__).resolve().parent.parent / "actions"
LOG = logging.getLogger("test")


def _mk(d, size_kb=4):
    d.mkdir(parents=True, exist_ok=True)
    (d / "f.bin").write_bytes(b"x" * size_kb * 1024)


def test_run_action_supports_python_scripts(tmp_path):
    p = tmp_path / "hello.py"
    p.write_text('import sys; print("hi " + sys.argv[1])\n')
    r = run_action(tmp_path, Action("hello", "Hello", ("x",)), LOG)
    assert r.ok and r.output == "hi x"


def test_clean_jetbrains_old_removes_plugins_and_caches_but_keeps_settings(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _mk(home / ".config" / "JetBrains" / "GoLand2025.2", 1024)
    _mk(home / ".cache" / "JetBrains" / "GoLand2025.2", 1024)
    _mk(home / ".local" / "share" / "JetBrains" / "GoLand2025.2", 1024)
    _mk(home / ".config" / "JetBrains" / "GoLand2026.1")
    monkeypatch.setenv("AA_HOME", str(home))
    r = run_action(ACTIONS, Action("clean-jetbrains-old", "Remove old versions", ("GoLand2025.2",)), LOG)
    assert r.ok and r.output == "Removed: GoLand2025.2 (2 MB)"
    assert not (home / ".cache" / "JetBrains" / "GoLand2025.2").exists()
    assert not (home / ".local" / "share" / "JetBrains" / "GoLand2025.2").exists()
    assert (home / ".config" / "JetBrains" / "GoLand2025.2").exists()   # settings are never deleted
    assert (home / ".config" / "JetBrains" / "GoLand2026.1").exists()


def test_clean_jetbrains_old_keeps_everything_after_the_update_morning(tmp_path, monkeypatch, check_runner):
    """The 2026-11-08 case: an IDE update to 2026.3 next to the old 2026.2 and a manual settings backup."""
    home = tmp_path / "home"
    for rel in ("IntelliJIdea2026.2", "IntelliJIdea2026.3", "IntelliJIdea2026.3.pred-importem", "IntelliJIdea2026.3-backup"):
        _mk(home / ".config" / "JetBrains" / rel)
    _mk(home / ".cache" / "JetBrains" / "IntelliJIdea2026.2")
    monkeypatch.setenv("AA_HOME", str(home))
    f = check_runner.run("jetbrains-old", {"AA_HOME": str(home)})
    assert [i["name"] for i in f.items] == ["IntelliJIdea2026.2"]
    assert all("/.config/" not in p for p in f.items[0]["paths"])
    r = run_action(ACTIONS, Action("clean-jetbrains-old", "Remove old versions", ("IntelliJIdea2026.2",)), LOG)
    assert r.ok
    assert sorted(p.name for p in (home / ".config" / "JetBrains").iterdir()) == [
        "IntelliJIdea2026.2", "IntelliJIdea2026.3", "IntelliJIdea2026.3-backup", "IntelliJIdea2026.3.pred-importem"]
    for backup in ("IntelliJIdea2026.3-backup", "IntelliJIdea2026.3.pred-importem"):
        r = run_action(ACTIONS, Action("clean-jetbrains-old", "Remove old versions", (backup,)), LOG)
        assert not r.ok and (home / ".config" / "JetBrains" / backup).exists()


def test_clean_jetbrains_old_needs_a_newer_settings_directory(tmp_path, monkeypatch, check_runner):
    home = tmp_path / "home"
    _mk(home / ".cache" / "JetBrains" / "GoLand2025.2")
    _mk(home / ".config" / "JetBrains" / "GoLand2025.2")   # no newer GoLand has started yet
    monkeypatch.setenv("AA_HOME", str(home))
    assert check_runner.run("jetbrains-old", {"AA_HOME": str(home)}).severity == "none"
    r = run_action(ACTIONS, Action("clean-jetbrains-old", "Remove old versions", ("GoLand2025.2",)), LOG)
    assert not r.ok and (home / ".cache" / "JetBrains" / "GoLand2025.2").exists()


def test_clean_jetbrains_old_refuses_current_version(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _mk(home / ".config" / "JetBrains" / "GoLand2025.2")
    _mk(home / ".config" / "JetBrains" / "GoLand2026.1")
    _mk(home / ".cache" / "JetBrains" / "GoLand2026.1")
    monkeypatch.setenv("AA_HOME", str(home))
    r = run_action(ACTIONS, Action("clean-jetbrains-old", "Remove old versions", ("GoLand2026.1",)), LOG)
    assert not r.ok and r.output == "Refused: GoLand2026.1 is the current version"
    assert (home / ".config" / "JetBrains" / "GoLand2026.1").exists()


def test_clean_user_cache_refuses_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("AA_HOME", str(tmp_path))
    _mk(tmp_path / ".cache" / "foo")
    r = run_action(ACTIONS, Action("clean-user-cache", "Clean cache", ("foo",)), LOG)
    assert not r.ok and r.output == "Refused: foo is not in the list of safe caches"
    assert (tmp_path / ".cache" / "foo").exists()


def test_clean_user_cache_removes_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("AA_HOME", str(tmp_path))
    _mk(tmp_path / ".cache" / "yay", 1024)
    _mk(tmp_path / ".cache" / "pip", 1024)
    r = run_action(ACTIONS, Action("clean-user-cache", "Clean cache", ("yay", "pip")), LOG)
    assert r.ok and r.output == "Removed 2 caches, freed 2 MB"
    assert not (tmp_path / ".cache" / "yay").exists() and not (tmp_path / ".cache" / "pip").exists()


def test_clean_pkg_cache(shim, monkeypatch):
    shim.sudo()
    shim.cmd("paccache", "==> finished: 12 packages removed (disk space saved: 3.2 GiB)\n")
    shim.cmd("yay", "")
    state = shim.bin_dir / "du.state"
    shim.cmd("du", script=f'if [[ -f "{state}" ]]; then printf "100\\t/var/cache/pacman/pkg\\n50\\t/x/yay\\n"; else touch "{state}"; printf "11446\\t/var/cache/pacman/pkg\\n4190\\t/x/yay\\n"; fi\n')
    r = run_action(ACTIONS, Action("clean-pkg-cache", "Clean package caches"), LOG)
    assert r.ok and r.output == "Freed 15486 MB (pacman cache kept 2 versions, yay cache cleaned)"
    assert any(c.startswith("sudo /usr/bin/paccache -rk2") for c in shim.calls())
    assert any(c.startswith("yay -Sc --noconfirm") for c in shim.calls())


def test_remove_orphans(shim):
    shim.sudo()
    shim.cmd("pacman", "")
    r = run_action(ACTIONS, Action("remove-orphans", "Remove orphans", ("a", "b")), LOG)
    assert r.ok and r.output == "Removed 2 orphan packages: a, b"
    assert shim.calls()[0] == "sudo /usr/bin/pacman -Rns --noconfirm a b"


def test_remove_orphans_failure(shim):
    shim.sudo()
    shim.cmd("pacman", "", exit_code=1, stderr="error: target not found: a")
    r = run_action(ACTIONS, Action("remove-orphans", "Remove orphans", ("a",)), LOG)
    assert not r.ok and r.output == "Removing orphans failed: error: target not found: a"
