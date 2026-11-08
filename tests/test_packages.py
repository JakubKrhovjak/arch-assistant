"""Installed / Browse tabs and their data: inventory, snapshot line, search parsing, install and uninstall actions."""
import logging
from datetime import datetime
from pathlib import Path

import pytest

from helpers import close_tray, make_tray, wait_until

from arch_assistant import catalog as K
from arch_assistant import cycle as C
from arch_assistant import inventory as I
from arch_assistant import snapshots as S
from arch_assistant.actions import NEVER_AUTO, TERMINAL_ACTIONS, run_action
from arch_assistant.model import Action, ActionResult, Finding

ACTIONS = Path(__file__).resolve().parent.parent / "actions"
LOG = logging.getLogger("test")


# --- inventory ---------------------------------------------------------------------------------

DESKTOPS = {
    "/usr/share/applications/org.kde.dolphin.desktop": "[Desktop Entry]\nType=Application\nName=Dolphin\nName[cs]=X\nIcon=system-file-manager\n",
    "/usr/share/applications/libreoffice-writer.desktop": "[Desktop Entry]\nType=Application\nName=LibreOffice Writer\nIcon=lo-writer\n",
    "/usr/share/applications/libreoffice-startcenter.desktop": "[Desktop Entry]\nType=Application\nName=LibreOffice\nIcon=lo\n",
    "/usr/share/applications/hidden.desktop": "[Desktop Entry]\nType=Application\nName=Hidden\nNoDisplay=true\n",
    "/usr/share/applications/kcm_mouse.desktop": "[Desktop Entry]\nType=Application\nName=Mouse\n",
    "/usr/share/applications/org.kde.plasma.emojier.desktop": "[Desktop Entry]\nType=Application\nName=Emoji Selector\n",
}
QL = "\n".join(f"{pkg} {path}" for pkg, path in [
    ("dolphin", "/usr/bin/dolphin"), ("dolphin", "/usr/share/applications/org.kde.dolphin.desktop"),
    ("libreoffice-fresh", "/usr/share/applications/libreoffice-writer.desktop"),
    ("libreoffice-fresh", "/usr/share/applications/libreoffice-startcenter.desktop"),
    ("ripgrep", "/usr/share/applications/hidden.desktop"),
    ("plasma-desktop", "/usr/share/applications/kcm_mouse.desktop"),
    ("plasma-desktop", "/usr/share/applications/org.kde.plasma.emojier.desktop"),
])


def _pacman_shim(shim):
    shim.cmd("pacman", script=(
        'case "$1" in\n'
        '  -Qe) printf "dolphin 26.08-1\\nlibreoffice-fresh 26.8-1\\nripgrep 15-1\\nlinux 7.2-1\\nbluedevil 6.8-1\\nyay 13-1\\nplasma-desktop 6.8-1\\n" ;;\n'
        '  -Qm) printf "yay 13-1\\nlocalsend-bin 1.18-2\\n" ;;\n'
        '  -Qg) printf "plasma bluedevil\\nkde-applications dolphin\\n" ;;\n'
        f'  -Ql) printf "%s\\n" "{QL}" ;;\n'
        'esac\n'))
    shim.cmd("flatpak", "com.google.Chrome\tGoogle Chrome\t157\tsystem\norg.foo.Bar\t\t1.0\tuser\n")


def test_parse_desktop_skips_hidden_and_non_apps():
    assert I.parse_desktop(DESKTOPS["/usr/share/applications/org.kde.dolphin.desktop"]) == {"name": "Dolphin", "icon": "system-file-manager"}
    assert I.parse_desktop(DESKTOPS["/usr/share/applications/hidden.desktop"]) is None
    assert I.parse_desktop("[Desktop Entry]\nType=Link\nName=X\n") is None
    assert I.parse_desktop("[Desktop Action new]\nName=X\n[Desktop Entry]\nType=Application\nName=Y\n")["name"] == "Y"


def test_collect_groups_and_sorts(shim):
    _pacman_shim(shim)
    inv = I.collect(read=lambda p: DESKTOPS.get(p, ""))
    assert inv.errors == []
    rows = {r.id: r for r in inv.rows}
    assert rows["dolphin"].name == "Dolphin" and rows["dolphin"].category == "apps" and rows["dolphin"].icon == "system-file-manager"
    assert rows["libreoffice-fresh"].name == "LibreOffice"
    assert rows["ripgrep"].category == "utilities" and rows["ripgrep"].name == "ripgrep"
    assert rows["plasma-desktop"].category == "system" and rows["plasma-desktop"].name == "plasma-desktop"
    assert rows["linux"].protected and rows["bluedevil"].protected                 # pattern and group member
    assert rows["yay"].source == "aur" and rows["yay"].protected and rows["localsend-bin"].source == "aur"
    assert rows["com.google.Chrome"].category == "apps" and rows["org.foo.Bar"].name == "org.foo.Bar"
    assert rows["org.foo.Bar"].installation == "user"
    assert [r.category for r in inv.rows] == sorted((r.category for r in inv.rows), key=I.CATEGORIES.index)
    apps = [r.name for r in inv.rows if r.category == "apps"]
    assert apps == sorted(apps, key=str.lower)
    assert rows["dolphin"].arg == "repo:dolphin" and rows["com.google.Chrome"].arg == "flatpak:com.google.Chrome"


def test_collect_reports_pacman_failure(shim):
    shim.cmd("pacman", exit_code=1)
    shim.cmd("flatpak", "com.google.Chrome\tGoogle Chrome\t157\tsystem\n")
    inv = I.collect()
    assert inv.errors == ["pacman"] and [r.id for r in inv.rows] == ["com.google.Chrome"]


# --- snapshots ---------------------------------------------------------------------------------

LISTING = """Device : /dev/dm-0
Status : OK

Num     Name                 Tags  Description
------------------------------------------------------------------------------
0    >  2026-11-03_04-57-32  O     {timeshift-autosnap} {created before upgrade}
1    >  2026-11-06_04-33-20  D
2    >  2026-11-07_07-34-35  B
3    >  2026-11-08_07-31-30  O     {timeshift-autosnap} {created before upgrade}
"""
NOW = datetime(2026, 11, 8, 12, 0)


def test_parse_list_and_autosnap_conf():
    snaps = S.parse_list(LISTING)
    assert [s.at.day for s in snaps] == [3, 6, 7, 8] and snaps[1].tags == "D" and snaps[1].description == ""
    conf = S.parse_autosnap_conf("# minHoursBetweenSnapshots=1\nminHoursBetweenSnapshots=6\nskipAutosnap=false\n")
    assert conf.min_hours == 6 and not conf.skip and conf.description.startswith("{timeshift-autosnap}")
    assert S.parse_autosnap_conf("minHoursBetweenSnapshots=abc\n").min_hours == 18


def test_snapshot_lines_within_and_after_the_wait():
    snaps = S.parse_list(LISTING)
    conf = S.parse_autosnap_conf("minHoursBetweenSnapshots=18\n")
    lines = S.snapshot_lines(snaps, conf, NOW)
    assert lines[0] == "Last snapshot: today 07:31 (before upgrade)"
    assert "made one today 07:31" in lines[1] and "waits 18 h (until tomorrow 01:31)" in lines[1]
    later = S.snapshot_lines(snaps, conf, datetime(2026, 11, 9, 2, 0))
    assert later == ["Last snapshot: yesterday 07:31 (before upgrade)",
                     "timeshift-autosnap takes a new snapshot before the upgrade"]
    assert S.snapshot_lines(snaps[:3], None, NOW) == ["Last snapshot: yesterday 07:34 (boot)"]
    assert "switched off" in S.snapshot_lines(snaps, S.parse_autosnap_conf("skipAutosnap=true"), NOW)[1]
    assert S.format_when(datetime(2026, 11, 3, 4, 57), NOW) == "Nov 3 04:57"


def test_snapshot_info_uses_passwordless_sudo(shim, tmp_path):
    shim.cmd("sudo", LISTING)
    conf = tmp_path / "autosnap.conf"
    conf.write_text("minHoursBetweenSnapshots=2\n")
    lines = S.snapshot_info(NOW, conf_path=conf, autosnap_bin=tmp_path / "none")
    assert shim.calls() == ["sudo -n /usr/bin/timeshift --list"]
    assert lines[1] == "timeshift-autosnap takes a new snapshot before the upgrade"
    shim.cmd("sudo", exit_code=1, stderr="sudo: a password is required")
    assert S.snapshot_info(NOW, conf_path=conf) is None


# --- search ------------------------------------------------------------------------------------

PACMAN_SS = """extra/vlc 3.0.24-1 [installed]
    Free and open source cross-platform multimedia player
extra/vlc-plugin-aom 3.0.24-1 (vlc-plugins)
    AV1 decoder
extra/phonon-qt6-vlc 0.12.0-6 [installed: 0.12.0-5]
    Phonon VLC backend
"""
FLATPAK_SS = ("org.videolan.VLC\tVLC\tVLC media player\t3.0.24\tflathub\n"
              "org.videolan.VLC.Locale\tTranslations\t\t\tflathub\n"
              "org.example.vlcfan\tVLC fan\tfan\t1.0\tflathub,other\n")
AUR = {"resultcount": 2, "type": "search", "results": [
    {"Name": "vlc-git", "Version": "4.0-1", "Description": "VLC from git", "NumVotes": 50, "Popularity": 0.5},
    {"Name": "vlc-nightly", "Version": "4.1-1", "Description": None, "NumVotes": 3, "Popularity": 2.5},
]}


def test_parse_pacman_ss():
    rows = K.parse_pacman_ss(PACMAN_SS, set())
    assert [(r.id, r.version, r.installed, r.origin) for r in rows] == [
        ("vlc", "3.0.24-1", True, "extra"), ("vlc-plugin-aom", "3.0.24-1", False, "extra"),
        ("phonon-qt6-vlc", "0.12.0-6", True, "extra")]
    assert rows[1].description == "AV1 decoder" and rows[0].arg == "repo:vlc"


def test_parse_aur_and_flatpak():
    aur = K.parse_aur(AUR, {"vlc-git"})
    assert [(r.id, r.installed, r.votes) for r in aur] == [("vlc-git", True, 50), ("vlc-nightly", False, 3)]
    assert aur[1].description == "" and aur[0].arg == "aur:vlc-git"
    fp = K.parse_flatpak_search(FLATPAK_SS, {"org.example.vlcfan"})
    assert [(r.id, r.name, r.installed, r.origin) for r in fp] == [
        ("org.videolan.VLC", "VLC", False, "flathub"), ("org.example.vlcfan", "VLC fan", True, "flathub")]
    assert K.parse_flatpak_search("No matches found\n", set()) == []


def test_search_merges_sources_by_relevance(shim):
    shim.cmd("pacman", script='[ "$1" = -Qq ] && { echo vlc; exit 0; }\n' f"printf '%s' '{PACMAN_SS}'\n")
    shim.cmd("flatpak", script='[ "$1" = list ] && exit 0\n' f"printf '%s' '{FLATPAK_SS}'\n")
    urls = []
    found = K.search("  VLC!  ", fetch=lambda url: urls.append(url) or AUR)
    assert found.term == "VLC" and found.errors == []
    assert urls == ["https://aur.archlinux.org/rpc/v5/search/VLC?by=name-desc"]
    ids = [r.id for r in found.results]
    assert ids[:2] == ["vlc", "org.videolan.VLC"]                     # exact names first
    assert ids.index("vlc-nightly") < ids.index("vlc-git")             # AUR by popularity
    assert any("pacman -Ss -- VLC" in c for c in shim.calls())


def test_search_keeps_other_sources_when_one_fails(shim):
    shim.cmd("pacman", script='[ "$1" = -Qq ] && exit 0\n' f"printf '%s' '{PACMAN_SS}'\n")
    shim.cmd("flatpak", exit_code=1)

    def offline(url):
        raise OSError("network unreachable")

    found = K.search("vlc", fetch=offline)
    assert {r.source for r in found.results} == {"repo"}
    assert any(e.startswith("AUR") for e in found.errors) and any(e.startswith("Flatpak") for e in found.errors)
    assert K.search("v", fetch=offline).results == []                 # below MIN_TERM: nothing runs


# --- actions -----------------------------------------------------------------------------------

def test_install_and_uninstall_are_never_auto_and_visible():
    assert {"install", "uninstall"} <= NEVER_AUTO and {"install", "uninstall"} <= TERMINAL_ACTIONS


@pytest.mark.parametrize("action", ["install", "uninstall"])
@pytest.mark.parametrize("args", [(), ("pkg",), ("pkg:vlc",), ("repo:-Rdd",), ("flatpak:--system",), ("repo:a b",),
                                  ("bogus:x",), ("repo:vlc", "aur:")])
def test_package_actions_reject_bad_args_before_running(shim, action, args):
    shim.sudo()
    shim.cmd("pacman")
    shim.cmd("yay")
    shim.cmd("flatpak")
    r = run_action(ACTIONS, Action(action, action, args), LOG)
    assert not r.ok and r.output.startswith(f"{action.capitalize()} failed: ")
    assert shim.calls() == []


def test_uninstall_guards_then_runs_pacman_then_flatpak_by_installation(shim):
    shim.sudo()
    shim.pacman_removal("vlc\nripgrep\nlibvlc\n")
    shim.cmd("flatpak", script='[ "$1" = list ] && echo org.user.App\nexit 0\n')
    r = run_action(ACTIONS, Action("uninstall", "Uninstall", ("repo:vlc", "aur:ripgrep", "flatpak:com.google.Chrome",
                                                               "flatpak:org.user.App")), LOG)
    assert r.ok and r.output == "Removed 2 packages and 2 Flatpaks"
    calls = shim.calls()
    assert calls[0] == "pacman -Rs --print --print-format %n vlc ripgrep"       # the guard, before any sudo
    assert "sudo /usr/bin/pacman -Rns --noconfirm vlc ripgrep" in calls
    assert "sudo /usr/bin/flatpak uninstall --system -y --noninteractive com.google.Chrome" in calls
    assert "flatpak uninstall --user -y --noninteractive org.user.App" in calls
    assert not any(c.startswith("sudo") and "org.user.App" in c for c in calls)


def _fails(name, stderr):
    return {name: {"exit_code": 1, "stderr": stderr}}


# action, args, failing shims, expected stdout, the exact command that failed
FAILURES = [
    ("install", ("repo:vlc", "aur:yay-bin"), _fails("pacman", "error: target not found: vlc"),
     "Install failed: error: target not found: vlc", "sudo /usr/bin/pacman -S --needed --noconfirm vlc"),
    ("install", ("repo:vlc", "aur:broken"), _fails("yay", "error: PKGBUILD failed"),
     "Installed 1 packages (repo); AUR install failed: error: PKGBUILD failed",
     "yay -S --aur --needed --noconfirm broken"),
    ("install", ("aur:x", "flatpak:org.videolan.VLC"), _fails("flatpak", "error: No remote refs found for VLC"),
     "Installed 0 packages (repo), 1 (AUR); Flatpak install failed: error: No remote refs found for VLC",
     "sudo /usr/bin/flatpak install --system -y --noninteractive flathub org.videolan.VLC"),
    ("uninstall", ("repo:vlc",), {}, "Uninstall failed: error: failed to prepare transaction (could not satisfy dependencies)",
     "sudo /usr/bin/pacman -Rns --noconfirm vlc"),
    ("uninstall", ("flatpak:com.google.Chrome",), _fails("flatpak", "error: com.google.Chrome not installed"),
     "Removed 0 packages; Flatpak uninstall failed: error: com.google.Chrome not installed",
     "sudo /usr/bin/flatpak uninstall --system -y --noninteractive com.google.Chrome"),
]


@pytest.mark.parametrize("case", FAILURES)
def test_package_action_failures_report_the_first_error(shim, case):
    action, args, failing, output, command = case
    shim.sudo()
    shim.pacman_removal("vlc\n", rns_stderr="error: failed to prepare transaction (could not satisfy dependencies)")
    for name in ("pacman", "yay", "flatpak"):
        if name in failing:
            shim.cmd(name, **failing[name])
        elif name != "pacman":
            shim.cmd(name)
    r = run_action(ACTIONS, Action(action, action, args), LOG)
    assert not r.ok and r.output == output
    assert command in shim.calls()


def test_uninstall_user_flatpak_failure(shim):
    shim.sudo()
    shim.cmd("flatpak", script='[ "$1" = list ] && { echo org.user.App; exit 0; }\necho "error: busy" >&2\nexit 1\n')
    r = run_action(ACTIONS, Action("uninstall", "Uninstall", ("flatpak:org.user.App",)), LOG)
    assert not r.ok and r.output == "Removed 0 packages; Flatpak uninstall failed: error: busy"
    assert "flatpak uninstall --user -y --noninteractive org.user.App" in shim.calls()
    assert not any(c.startswith("sudo") for c in shim.calls())


def test_install_runs_each_source(shim):
    shim.sudo()
    shim.cmd("pacman")
    shim.cmd("yay")
    shim.cmd("flatpak")
    r = run_action(ACTIONS, Action("install", "Install", ("repo:vlc", "aur:localsend-bin", "flatpak:org.videolan.VLC")), LOG)
    assert r.ok and r.output == "Installed 1 packages (repo), 1 (AUR) and 1 Flatpaks"
    assert [c for c in shim.calls() if c.startswith(("sudo", "yay"))] == ["sudo /usr/bin/pacman -S --needed --noconfirm vlc",
                            "yay -S --aur --needed --noconfirm localsend-bin",
                            "sudo /usr/bin/flatpak install --system -y --noninteractive flathub org.videolan.VLC"]


# --- tabs --------------------------------------------------------------------------------------

def _inventory():
    rows = [I.Installed("Dolphin", "dolphin", "26.08-1", "repo", "apps", "system-file-manager"),
            I.Installed("Google Chrome", "com.google.Chrome", "157", "flatpak", "apps", "com.google.Chrome", "system"),
            I.Installed("ripgrep", "ripgrep", "15-1", "repo", "utilities"),
            I.Installed("localsend-bin", "localsend-bin", "1.18-2", "aur", "utilities"),
            I.Installed("linux", "linux", "7.2-1", "repo", "system")]
    return I.Inventory(rows)


@pytest.fixture
def window(qapp):
    from arch_assistant.ui.packages_window import PackagesWindow
    calls = {"load": 0, "uninstall": [], "install": [], "search": [], "confirm": []}

    def confirm(rows):
        calls["confirm"].append([r.id for r in rows])
        return calls.get("answer", True)

    w = PackagesWindow(None, on_update=lambda a: None, on_refresh=lambda: None,
                       on_load_installed=lambda: calls.__setitem__("load", calls["load"] + 1),
                       on_uninstall=calls["uninstall"].append, on_search=calls["search"].append,
                       on_install=calls["install"].append, confirm_install=confirm)
    w.calls = calls
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _ids(tab):
    return [r.id for r in tab.visible_rows()]


def test_installed_tab_loads_once_groups_and_protects(window):
    assert window.current() == "updates" and window.calls["load"] == 0
    window.show_tab("installed")
    assert window.calls["load"] == 1
    tab = window.installed_tab
    tab.set_inventory(_inventory())
    window.show_tab("updates")
    window.show_tab("installed")
    assert window.calls["load"] == 1                                   # loaded: no second read
    assert window.subtitle.text() == "5 installed: 2 apps, 2 utilities, 1 system"
    assert [h.text(0) for h in tab.headers.values()] == ["Apps  ·  2", "Utilities  ·  2", "System  ·  1"]
    assert [b.text() for b in tab.filter_buttons["category"].values()] == ["All  5", "Apps  2", "Utilities  2", "System  1"]
    tab.set_checked(("repo", "linux"), True)
    assert tab.selected_rows() == [] and not tab.primary.isEnabled()
    linux = next(item for item, r in tab.items if r.id == "linux")
    assert not linux.flags() & linux.flags().ItemIsUserCheckable and "Protected" in linux.toolTip(0)


def test_installed_tab_filters_and_hands_over_the_selection(window):
    from PySide6.QtCore import Qt
    tab = window.installed_tab
    tab.set_inventory(_inventory())
    tab.filter_buttons["category"]["utilities"].click()
    assert _ids(tab) == ["ripgrep", "localsend-bin"] and tab.headers["apps"].isHidden()
    tab.filter_buttons["source"]["aur"].click()
    assert _ids(tab) == ["localsend-bin"]
    tab.filter_buttons["category"]["all"].click()
    tab.filter_buttons["source"]["all"].click()
    tab.search.setText("CHROME")
    assert _ids(tab) == ["com.google.Chrome"]
    tab.search.setText("")
    next(item for item, r in tab.items if r.id == "com.google.Chrome").setCheckState(0, Qt.CheckState.Checked)
    tab.set_checked(("aur", "localsend-bin"), True)
    assert tab.primary.text() == "Uninstall selected (2)"
    tab.set_inventory(_inventory())                                    # a refresh keeps the picks
    tab.primary.click()
    # no confirmation in the tab: PackagesController computes the removal set, guards and confirms it
    assert window.calls["confirm"] == [] and window.calls["uninstall"] == [("flatpak:com.google.Chrome", "aur:localsend-bin")]
    tab.set_running(True, "Uninstalling…")
    assert not tab.primary.isEnabled() and not tab.busy.isHidden() and not tab.refresh_button.isEnabled()


def test_installed_row_click_toggles_and_single_uninstall(qapp, window):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    tab = window.installed_tab
    tab.set_inventory(_inventory())
    window.show_tab("installed")
    window.resize(1000, 600)
    window.show()
    qapp.processEvents()

    def click(row_id, x):
        item = next(i for i, r in tab.items if r.id == row_id)
        rect = tab.tree.visualItemRect(item)
        QTest.mouseClick(tab.tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(rect.left() + x, rect.center().y()))
        qapp.processEvents()

    click("ripgrep", 200)                                  # on the name, not the box
    assert [r.id for r in tab.selected_rows()] == ["ripgrep"]
    click("ripgrep", 200)
    assert tab.selected_rows() == []
    click("linux", 200)                                    # protected: stays unselected
    assert tab.selected_rows() == []
    rows = {r.id: r for _i, r in tab.items}
    tab.submit([rows["linux"], rows["localsend-bin"]])     # the context menu path; protected rows are dropped
    assert window.calls["uninstall"] == [("aur:localsend-bin",)]


def _found(term="vlc"):
    return K.SearchResult(term, [K.Result("vlc", "vlc", "3.0.24-1", "Player", "repo", True, "extra"),
                                 K.Result("VLC", "org.videolan.VLC", "3.0.24", "Player", "flatpak", False, "flathub"),
                                 K.Result("vlc-git", "vlc-git", "4.0-1", "Git", "aur", False, "AUR", 50, 0.5)])


def test_browse_tab_searches_marks_installed_and_installs(window):
    tab = window.browse_tab
    window.show_tab("browse")
    tab.search.setText("v")
    tab.start_search()
    assert window.calls["search"] == []                                # below the minimum
    tab.search.setText("vlc")
    tab.search.returnPressed.emit()
    assert window.calls["search"] == ["vlc"] and not tab.busy.isHidden()
    tab.set_results(_found("vl"))                                      # stale answer: dropped
    assert tab.data == []
    tab.set_results(_found())
    assert tab.busy.isHidden() and window.subtitle.text() == "3 results for “vlc”"
    installed = tab.items[0][0]
    assert installed.text(4) == "Installed" and not installed.flags() & installed.flags().ItemIsUserCheckable
    tab.set_checked(("repo", "vlc"), True)
    assert tab.action_args() is None
    assert not tab.aur_note.isHidden()                                 # an AUR result is shown
    tab.filter_buttons["source"]["flatpak"].click()
    assert _ids(tab) == ["org.videolan.VLC"] and tab.aur_note.isHidden()
    tab.filter_buttons["source"]["all"].click()
    tab.set_checked(("flatpak", "org.videolan.VLC"), True)
    tab.set_checked(("aur", "vlc-git"), True)
    assert tab.primary.text() == "Install selected (2)"
    window.calls["answer"] = False
    tab.primary.click()
    assert window.calls["install"] == [] and window.calls["confirm"] == [["org.videolan.VLC", "vlc-git"]]
    window.calls["answer"] = True
    tab.primary.click()
    assert window.calls["install"] == [("flatpak:org.videolan.VLC", "aur:vlc-git")]


def test_browse_picks_from_several_searches_add_up(window):
    tab = window.browse_tab
    tab.searching = "vlc"
    tab.set_results(_found())
    tab.set_checked(("aur", "vlc-git"), True)
    tab.searching = "chrome"
    tab.set_results(K.SearchResult("chrome", [K.Result("Chrome", "com.google.Chrome", "1", "", "flatpak")]))
    tab.set_checked(("flatpak", "com.google.Chrome"), True)
    assert tab.action_args() == ("flatpak:com.google.Chrome", "aur:vlc-git")


def test_confirm_dialogs_list_every_package_and_mark_dependencies(qapp):
    from arch_assistant.protection import RemovalPlan
    from arch_assistant.ui.confirm import install_box, refusal_box, removal_box
    rows = {r.id: r for r in _inventory().rows}
    plan = RemovalPlan((("repo", "ripgrep"),), ("com.google.Chrome",), ("ripgrep", "libpcre2", "pcre"))
    box, _yes = removal_box(plan, rows)
    assert box.text() == "Uninstall 2 packages and 2 dependencies?"
    lines = box.informativeText().splitlines()
    assert lines[:2] == ["• ripgrep – Arch", "• Google Chrome (com.google.Chrome) – Flatpak"]
    assert "• libpcre2 (dependency)" in lines and "• pcre (dependency)" in lines
    box, _yes = install_box([_found().results[2]])
    assert box.text() == "Install 1 package?" and box.informativeText().startswith("• vlc-git – AUR")
    blocked = RemovalPlan((("repo", "zsh-completions"),), (), ("zsh-completions", "zsh"), (("zsh", "login shell"),))
    box = refusal_box(blocked.problem, blocked)
    assert "• zsh – login shell" in box.informativeText() and "zsh" in box.detailedText()


def test_removal_confirmation_keeps_the_source_when_the_installed_tab_never_loaded(qapp, shim):
    """An unused-apps notification for an AUR package, confirmed before the Packages window was ever opened."""
    from arch_assistant.protection import removal_plan
    from arch_assistant.ui.confirm import removal_box
    shim.pacman_removal("oldapp\nyay-thing\n")
    plan = removal_plan(["aur:oldapp", "repo:yay-thing"])
    assert plan.picked == (("aur", "oldapp"), ("repo", "yay-thing")) and plan.names == ("oldapp", "yay-thing")
    box, _yes = removal_box(plan, {})
    assert box.informativeText().splitlines()[:2] == ["• oldapp – AUR", "• yay-thing – Arch"]


# --- tray wiring -------------------------------------------------------------------------------

@pytest.fixture
def tray(qapp, tmp_path, monkeypatch):
    app, _transport = make_tray(tmp_path, LOG)
    monkeypatch.setattr(app.packages, "load_snapshot", lambda: None)
    app.asked, app.refused = [], []
    app.packages.confirm_removal = lambda plan, known, parent=None: app.asked.append((plan, known)) or app.answer
    app.packages.show_refusal = lambda problem, plan, parent=None: app.refused.append(problem)
    app.answer = True
    assert wait_until(qapp, lambda: not app.busy)   # the first check cycle starts on the first processEvents
    yield app
    close_tray(app, qapp)


def _plan(monkeypatch, plan):
    from arch_assistant.ui import packages_controller as TR
    seen = []
    monkeypatch.setattr(TR, "removal_plan", lambda args: seen.append(tuple(args)) or plan)
    return seen


def test_installed_tab_reads_in_a_worker(qapp, tray, monkeypatch):
    from arch_assistant.ui import packages_controller as TR
    monkeypatch.setattr(TR, "collect", _inventory)
    tray.updates_loaded = True
    tray.show_installed()
    assert tray.packages_window.current() == "installed"
    assert wait_until(qapp, lambda: tray.packages_window.installed_tab.loaded)
    assert not tray.packages.inventory_loading and not tray.packages_window.installed_tab.running


def test_uninstall_confirms_the_real_removal_set_then_runs_and_refreshes(qapp, tray, monkeypatch):
    from arch_assistant.protection import RemovalPlan
    tray.updates_loaded = True
    tray.show_installed()
    ran, loads, refreshes, searches = [], [], [], []
    monkeypatch.setattr(tray, "run_pending", lambda p, auto=False, confirmed=False: ran.append((p, confirmed)))
    monkeypatch.setattr(tray.packages, "load_installed", lambda: loads.append(True))
    monkeypatch.setattr(tray, "refresh_updates", lambda: refreshes.append(True))
    seen = _plan(monkeypatch, RemovalPlan((("repo", "ripgrep"),), (), ("ripgrep", "libpcre2")))
    w = tray.packages_window
    w.installed_tab.set_inventory(_inventory())
    tray.packages.uninstall_selected(("repo:ripgrep",))
    assert w.installed_tab.running and "Checking what would be removed" in w.installed_tab.status.text()
    assert wait_until(qapp, lambda: ran)
    assert seen == [("repo:ripgrep",)] and tray.asked[0][0].dependencies == ("libpcre2",)
    assert tray.asked[0][1]["ripgrep"].name == "ripgrep"               # names for the dialog come from the tab
    p, confirmed = ran[0]
    assert p.action == Action("uninstall", "Uninstall packages", ("repo:ripgrep",)) and confirmed
    assert w.installed_tab.running and not tray.removal_waiting
    tray._on_action_finished(p, ActionResult("uninstall", True, "Removed 1 packages and 0 Flatpaks", "", 1.0), [])
    assert not w.installed_tab.running and loads == [True] and refreshes == [True]

    w.browse_tab.searching = "vlc"
    w.browse_tab.set_results(_found())
    monkeypatch.setattr(tray.packages, "search_packages", searches.append)
    tray.packages.install_selected(("flatpak:org.videolan.VLC",))
    assert ran[1][0].action.id == "install" and w.browse_tab.running
    tray._on_action_finished(ran[1][0], ActionResult("install", False, "Install failed", "", 1.0), [])
    assert not w.browse_tab.running and "failed" in w.browse_tab.status.text()


def test_blocked_or_cancelled_uninstall_never_runs(qapp, tray, monkeypatch):
    from arch_assistant.protection import RemovalPlan
    tray.updates_loaded = True
    tray.show_installed()
    ran = []
    monkeypatch.setattr(tray, "run_pending", lambda p, auto=False, confirmed=False: ran.append(p))
    tab = tray.packages_window.installed_tab
    _plan(monkeypatch, RemovalPlan((("repo", "zsh-completions"),), (), ("zsh-completions", "zsh"), (("zsh", "login shell"),)))
    tray.packages.uninstall_selected(("repo:zsh-completions",))
    assert wait_until(qapp, lambda: tray.refused)
    assert tray.refused == ["blocked: zsh (login shell)"] and tray.asked == [] and ran == []
    assert not tab.running and tab.status.text() == "Not uninstalled: blocked: zsh (login shell)"

    _plan(monkeypatch, RemovalPlan((("repo", "ripgrep"),), (), ("ripgrep",)))
    tray.answer = False
    tray.packages.uninstall_selected(("repo:ripgrep",))
    assert wait_until(qapp, lambda: tray.asked)
    assert ran == [] and not tab.running and not tray.removal_waiting


def test_pending_uninstall_from_a_check_gets_the_same_guard(qapp, tray, monkeypatch):
    from arch_assistant.protection import RemovalPlan
    started = []
    monkeypatch.setattr(tray.cycle, "run_pending", lambda p: started.append(p) or (ActionResult("uninstall", True, "Removed", "", 0.1), []))
    seen = _plan(monkeypatch, RemovalPlan((("repo", "oldapp"),), (), ("oldapp",)))
    f = Finding("unused-apps", "low", "1 application unused", "", ({"name": "oldapp"},),
                (Action("uninstall", "Uninstall Old App (oldapp)", ("repo:oldapp",)),))
    p = C.Pending(f, f.actions[0])
    tray.run_pending(p)
    assert started == [] and tray.removal_waiting
    assert wait_until(qapp, lambda: started)
    assert seen == [("repo:oldapp",)] and len(tray.asked) == 1 and started[0].action.args == ("repo:oldapp",)


def test_package_action_while_busy_only_says_so(tray, monkeypatch):
    tray.updates_loaded = True
    tray.show_installed()
    ran = []
    monkeypatch.setattr(tray, "run_pending", lambda p, auto=False, confirmed=False: ran.append(p))
    tray.busy = True
    tray.packages.uninstall_selected(("repo:ripgrep",))
    assert ran == [] and not tray.removal_waiting and "busy" in tray.packages_window.installed_tab.status.text()
