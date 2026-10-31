import logging
from pathlib import Path

from arch_assistant.actions import run_action
from arch_assistant.model import Action

ACTIONS = Path(__file__).resolve().parent.parent / "actions"
LOG = logging.getLogger("test")
SUDO = 'cmd=$(basename "$1"); shift; exec "$cmd" "$@"\n'


def _mk(d, size_kb=4):
    d.mkdir(parents=True, exist_ok=True)
    (d / "f.bin").write_bytes(b"x" * size_kb * 1024)


def _toolbox(home, app, body):
    d = home / ".local/share/JetBrains/Toolbox/apps" / app / "ch-0" / "1"
    d.mkdir(parents=True)
    (d / "product-info.json").write_text(body)


def _info(*names):
    return "{" + ", ".join(f'"dataDirectoryName": "{n}"' for n in names) + "}"


def _old(check_runner, home):
    return [i["name"] for i in check_runner.run("jetbrains-old", {"AA_HOME": str(home)}).items]


def test_jetbrains_toolbox_installed_dir_is_never_old(check_runner, tmp_path):
    home = tmp_path / "home"
    _toolbox(home, "pycharm", _info("PyCharm2025.2"))
    for rel in ("PyCharm2025.2", "PyCharm2025.1", "CLion2025.2", "IntelliJIdea2026.1-backup"):
        _mk(home / ".config/JetBrains" / rel)
    _mk(home / ".cache/JetBrains/PyCharm2025.2")
    assert sorted(_old(check_runner, home)) == ["CLion2025.2", "IntelliJIdea2026.1-backup", "PyCharm2025.1"]


def test_jetbrains_toolbox_collects_every_data_directory_name(check_runner, tmp_path):
    home = tmp_path / "home"
    _toolbox(home, "idea", '{"a": {"dataDirectoryName": "IntelliJIdea2026.1"}, "b": {"dataDirectoryName": "IntelliJIdea2026.2"}}')
    for rel in ("IntelliJIdea2026.1", "IntelliJIdea2026.2", "IntelliJIdea2025.3"):
        _mk(home / ".config/JetBrains" / rel)
    assert _old(check_runner, home) == ["IntelliJIdea2025.3"]


def test_jetbrains_fallback_highest_version_without_toolbox_info(check_runner, tmp_path):
    home = tmp_path / "home"
    for rel in ("GoLand2025.2", "GoLand2026.1", "GoLand2026.1-backup"):
        _mk(home / ".config/JetBrains" / rel)
    assert _old(check_runner, home) == ["GoLand2025.2", "GoLand2026.1-backup"]


def test_jetbrains_uninstalled_single_version_is_old(check_runner, tmp_path):
    home = tmp_path / "home"
    _toolbox(home, "pycharm", _info("PyCharm2025.2"))
    _mk(home / ".config/JetBrains/CLion2025.2")
    f = check_runner.run("jetbrains-old", {"AA_HOME": str(home)})
    assert [i["name"] for i in f.items] == ["CLion2025.2"]
    assert "not installed" in f.summary


def test_clean_jetbrains_old_refuses_installed_and_deletes_old(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AA_HOME", str(home))
    _toolbox(home, "pycharm", _info("PyCharm2025.2"))
    _mk(home / ".config/JetBrains/PyCharm2025.2")
    _mk(home / ".config/JetBrains/CLion2025.2")
    r = run_action(ACTIONS, Action("clean-jetbrains-old", "x", ("PyCharm2025.2",)), LOG)
    assert not r.ok and (home / ".config/JetBrains/PyCharm2025.2").exists()
    r = run_action(ACTIONS, Action("clean-jetbrains-old", "x", ("CLion2025.2",)), LOG)
    assert r.ok and not (home / ".config/JetBrains/CLion2025.2").exists()
    assert (home / ".config/JetBrains/PyCharm2025.2").exists()


def test_toolbox_cache_reported_and_cleaned(check_runner, tmp_path, monkeypatch):
    home = tmp_path / "home"
    _mk(home / ".cache/JetBrains/Toolbox/temp", 3 * 1024)
    _mk(home / ".cache/JetBrains/Toolbox/download", 2 * 1024)
    _mk(home / ".cache/JetBrains/Toolbox/other", 4096)
    _mk(home / ".cache/JetBrains/PyCharm2025.2", 4096)
    f = check_runner.run("toolbox-cache", {"AA_HOME": str(home), "AA_TOOLBOX_CACHE_MB": "1"})
    assert f.severity == "low" and f.title == "JetBrains Toolbox leftovers: 5 MB"
    assert sorted(i["name"] for i in f.items) == ["download", "temp"]
    assert f.actions[0].id == "clean-toolbox-cache"
    monkeypatch.setenv("AA_HOME", str(home))
    r = run_action(ACTIONS, Action("clean-toolbox-cache", "x"), LOG)
    assert r.ok and r.output == "Removed Toolbox temp and download, freed 5 MB"
    assert not (home / ".cache/JetBrains/Toolbox/temp").exists()
    assert not (home / ".cache/JetBrains/Toolbox/download").exists()
    assert (home / ".cache/JetBrains/Toolbox/other").exists() and (home / ".cache/JetBrains/PyCharm2025.2").exists()


def test_toolbox_cache_none_when_small_or_missing(check_runner, tmp_path):
    home = tmp_path / "home"
    assert check_runner.run("toolbox-cache", {"AA_HOME": str(home)}).severity == "none"
    _mk(home / ".cache/JetBrains/Toolbox/temp", 10)
    assert check_runner.run("toolbox-cache", {"AA_HOME": str(home)}).severity == "none"


def _du(shim, tmp_path, *rows):
    shim.cmd("du", "".join(f"{size}\t{tmp_path}/.cache{name}\n" for size, name in rows))


def test_user_cache_offers_safe_caches_when_combined_above_limit(shim, check_runner, tmp_path):
    _du(shim, tmp_path, (3000, ""), (775, "/yay"), (988, "/go-build"), (900, "/foo"), (0, "/pip"))
    f = check_runner.run("user-cache", {"AA_USER_CACHE_GB": "1", "AA_HOME": str(tmp_path)})
    assert sorted(i["name"] for i in f.items) == ["go-build", "yay"]
    assert sorted(f.actions[0].args) == ["go-build", "yay"]


def test_user_cache_safe_caches_together_below_limit_not_offered(shim, check_runner, tmp_path):
    _du(shim, tmp_path, (3000, ""), (300, "/yay"), (400, "/go-build"), (900, "/foo"))
    assert check_runner.run("user-cache", {"AA_USER_CACHE_GB": "1", "AA_HOME": str(tmp_path)}).severity == "none"


def test_user_cache_non_safe_entry_only_above_limit(shim, check_runner, tmp_path):
    _du(shim, tmp_path, (3000, ""), (775, "/yay"), (988, "/go-build"), (1500, "/bar"), (900, "/foo"))
    f = check_runner.run("user-cache", {"AA_USER_CACHE_GB": "1", "AA_HOME": str(tmp_path)})
    assert sorted(i["name"] for i in f.items) == ["bar", "go-build", "yay"]
    assert sorted(f.actions[0].args) == ["go-build", "yay"]
