def _mk(d, size_kb=4):
    d.mkdir(parents=True, exist_ok=True)
    (d / "f.bin").write_bytes(b"x" * size_kb * 1024)


def _home(tmp_path):
    home = tmp_path / "home"
    for rel in ("GoLand2025.2", "GoLand2026.1", "IntelliJIdea2026.2", "IntelliJIdea2026.1-backup", "Daemon"):
        _mk(home / ".config" / "JetBrains" / rel, 1024)
    _mk(home / ".cache" / "JetBrains" / "GoLand2025.2", 2048)
    _mk(home / ".local" / "share" / "JetBrains" / "GoLand2025.2", 1024)
    _mk(home / ".local" / "share" / "JetBrains" / "Toolbox" / "apps" / "goland" / "bin", 10)
    return home


def test_jetbrains_old_finds_only_superseded_versions(check_runner, tmp_path):
    home = _home(tmp_path)
    f = check_runner.run("jetbrains-old", {"AA_HOME": str(home)})
    assert f.severity == "low"
    assert [i["name"] for i in f.items] == ["GoLand2025.2"]
    assert f.items[0]["size_mb"] == 4
    assert sorted(p.split("/")[-3] for p in f.items[0]["paths"]) == [".cache", ".config", "share"]
    assert f.actions[0].id == "clean-jetbrains-old" and f.actions[0].args == ("GoLand2025.2",)
    assert f.title == "Old JetBrains IDE versions: 4 MB"
    assert "GoLand 2025.2 (current 2026.1)" in f.summary


def test_jetbrains_old_none(check_runner, tmp_path):
    home = tmp_path / "home"
    _mk(home / ".config" / "JetBrains" / "GoLand2026.1")
    assert check_runner.run("jetbrains-old", {"AA_HOME": str(home)}).severity == "none"


PACCACHE = r'''
case "$*" in
  "-dk2") echo "==> finished dry run: 40 candidates (disk space saved: 1.5 GiB)" ;;
  "-duk0") echo "==> finished dry run: 12 candidates (disk space saved: 800.0 MiB)" ;;
  *) echo "==> no candidate packages found for pruning" ;;
esac
'''


def _du(pacman_mb, yay_mb):
    return (f'case "$*" in\n  *pacman/pkg*) printf "{pacman_mb}\\t/var/cache/pacman/pkg\\n" ;;\n'
            f'  *) printf "{yay_mb}\\t$HOME/.cache/yay\\n" ;;\nesac\n')


def test_pkg_cache_reports_reclaimable_space_only(shim, check_runner):
    shim.cmd("du", script=_du(9991, 1200))
    shim.cmd("paccache", script=PACCACHE)
    f = check_runner.run("pkg-cache", {"AA_PKG_CACHE_GB": "2"})
    assert f.severity == "low"
    by = {i["name"]: i["size_mb"] for i in f.items}
    assert by == {"old package versions": 1536, "uninstalled packages": 800, "yay build cache": 1200}
    assert f.title == "Package caches: 3536 MB can be freed"
    assert "9991 MB" in f.summary
    assert f.actions[0].id == "clean-pkg-cache"


def test_pkg_cache_large_but_nothing_reclaimable_is_quiet(shim, check_runner):
    shim.cmd("du", script=_du(9991, 0))
    shim.cmd("paccache", "==> no candidate packages found for pruning\n")
    f = check_runner.run("pkg-cache", {"AA_PKG_CACHE_GB": "2"})
    assert f.severity == "none" and "9991 MB" in f.title


def test_pkg_cache_below_threshold(shim, check_runner):
    shim.cmd("du", script=_du(500, 100))
    shim.cmd("paccache", script=PACCACHE)
    assert check_runner.run("pkg-cache", {"AA_PKG_CACHE_GB": "5"}).severity == "none"


def test_orphans_lists_packages(shim, check_runner):
    shim.cmd("pacman", "appstream-glib\ngperf\n")
    f = check_runner.run("orphans")
    assert f.severity == "low" and [i["name"] for i in f.items] == ["appstream-glib", "gperf"]
    assert f.actions[0].id == "remove-orphans" and f.actions[0].args == ("appstream-glib", "gperf")
    assert f.title == "2 orphan packages"


def test_orphans_none(shim, check_runner):
    shim.cmd("pacman", "", exit_code=1)
    assert check_runner.run("orphans").severity == "none"


def test_user_cache_marks_safe_entries(shim, check_runner, tmp_path):
    shim.cmd("du", f"21952\t{tmp_path}/.cache\n12851\t{tmp_path}/.cache/JetBrains\n4190\t{tmp_path}/.cache/yay\n2000\t{tmp_path}/.cache/foo\n300\t{tmp_path}/.cache/small\n")
    f = check_runner.run("user-cache", {"AA_USER_CACHE_GB": "1", "AA_HOME": str(tmp_path)})
    assert f.severity == "low"
    by = {i["name"]: i for i in f.items}
    assert set(by) == {"JetBrains", "yay", "foo"}
    assert by["yay"]["safe"] is True and by["JetBrains"]["safe"] is True and by["foo"]["safe"] is False
    assert f.actions[0].id == "clean-user-cache" and set(f.actions[0].args) == {"JetBrains", "yay"}


def test_user_cache_none(shim, check_runner, tmp_path):
    shim.cmd("du", f"500\t{tmp_path}/.cache\n300\t{tmp_path}/.cache/small\n")
    assert check_runner.run("user-cache", {"AA_USER_CACHE_GB": "1", "AA_HOME": str(tmp_path)}).severity == "none"
