"""Updates tab of the Packages window: repo packages all-or-nothing, selection → update.sh args, filter and search, tray wiring."""
import json
import logging
import os
import stat
import time

import pytest

from arch_assistant import cycle as C
from arch_assistant.config import load_config
from arch_assistant.model import Action, ActionResult, Finding, Summary
from arch_assistant.state import State

LOG = logging.getLogger("test")

ITEMS = (
    {"name": "vim", "old": "9.1-1", "new": "9.2-1", "source": "repo", "critical": False},
    {"name": "linux", "old": "7.2.10-arch1-1", "new": "7.2.11-arch1-1", "source": "repo", "critical": True},
    {"name": "git", "old": "2.50-1", "new": "2.51-1", "source": "repo", "critical": False},
    {"name": "mbpoll-git", "old": "1.5-1", "new": "1.6-1", "source": "aur", "critical": False},
    {"name": "yay-bin", "old": "12.4-1", "new": "12.5-1", "source": "aur", "critical": False},
    {"name": "com.google.Chrome", "old": "141", "new": "142", "source": "flatpak", "critical": False},
)


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pending(items=ITEMS, reason=None):
    f = Finding("updates", "high", f"{len(items)} pending updates", "S", tuple(items),
                (Action("update", "Update system"),))
    return C.Pending(f, f.actions[0], reason)


@pytest.fixture
def window(qapp):
    from arch_assistant.tray import PackagesWindow
    sent = []
    pw = PackagesWindow(_pending(), on_update=sent.append, on_refresh=lambda: None)
    w = pw.updates_tab
    w.sent = sent
    yield w
    pw.close()
    pw.deleteLater()
    qapp.processEvents()


def _row(w, name):
    return next(r for r, i in zip(w.rows(), w.items) if i["name"] == name)


# --- pure mapping ------------------------------------------------------------------------------

def test_update_args_mapping():
    from arch_assistant.tray import package_key, update_args
    keys = {package_key(i) for i in ITEMS}
    assert update_args(ITEMS, keys) == ()
    assert update_args(ITEMS, keys - {("aur", "yay-bin")}) == ("repo", "aur:mbpoll-git", "flatpak:com.google.Chrome")
    no_repo = {k for k in keys if k[0] != "repo"}
    assert update_args(ITEMS, no_repo) == ("aur:mbpoll-git", "aur:yay-bin", "flatpak:com.google.Chrome")
    assert update_args(ITEMS, {("repo", "vim")}) == ("repo",)
    assert update_args(ITEMS, set()) is None


# --- window ------------------------------------------------------------------------------------

def test_window_lists_versions_critical_first_all_checked(window):
    from PySide6.QtCore import Qt
    first = window.rows()[0]
    assert first.text(0) == "⚠ linux" and (first.text(1), first.text(2), first.text(3)) == ("7.2.10-arch1-1", "→", "7.2.11-arch1-1")
    assert [r.text(4) for r in window.rows()] == ["Arch", "Arch", "Arch", "AUR", "AUR", "Flatpak"]
    assert all(r.checkState(0) == Qt.CheckState.Checked for r in window.rows())
    assert window.summary == "6 updates available"
    assert window.update_button.text() == "Update selected (6)" and window.update_button.isEnabled()
    assert not window.repo_note.isHidden() and window.reason_label.isHidden()


def test_repo_rows_toggle_together_aur_individually(window):
    from PySide6.QtCore import Qt
    _row(window, "vim").setCheckState(0, Qt.CheckState.Unchecked)
    repo_states = [_row(window, n).checkState(0) for n in ("vim", "linux", "git")]
    assert repo_states == [Qt.CheckState.Unchecked] * 3
    assert _row(window, "mbpoll-git").checkState(0) == Qt.CheckState.Checked
    assert window.update_button.text() == "Update selected (3)"
    assert window.update_args() == ("aur:mbpoll-git", "aur:yay-bin", "flatpak:com.google.Chrome")

    _row(window, "git").setCheckState(0, Qt.CheckState.Checked)
    assert all(_row(window, n).checkState(0) == Qt.CheckState.Checked for n in ("vim", "linux", "git"))
    _row(window, "yay-bin").setCheckState(0, Qt.CheckState.Unchecked)
    assert _row(window, "mbpoll-git").checkState(0) == Qt.CheckState.Checked
    assert window.update_args() == ("repo", "aur:mbpoll-git", "flatpak:com.google.Chrome")


def test_update_button_sends_args_and_is_disabled_when_nothing_selected(window):
    window.update_button.click()
    assert window.sent == [()]                        # everything selected = the full update
    for key in [("repo", "vim"), ("aur", "mbpoll-git"), ("aur", "yay-bin"), ("flatpak", "com.google.Chrome")]:
        window.set_checked(key, False)
    assert window.update_args() is None and not window.update_button.isEnabled()
    window.set_checked(("flatpak", "com.google.Chrome"), True)
    window.update_button.click()
    assert window.sent[-1] == ("flatpak:com.google.Chrome",)
    window.set_running(True, "Updating…")
    assert not window.update_button.isEnabled() and not window.refresh_button.isEnabled() and not window.busy.isHidden()


def test_filter_and_search(window):
    assert [b.text() for b in window.filter_buttons.values()] == ["All  6", "Arch  3", "AUR  2", "Flatpak  1"]
    window.filter_buttons["aur"].click()
    assert window.visible_names() == ["mbpoll-git", "yay-bin"]
    window.search.setText("YAY")
    assert window.visible_names() == ["yay-bin"]
    window.filter_buttons["all"].click()
    window.search.setText("git")
    assert window.visible_names() == ["git", "mbpoll-git"]
    window.search.setText("")
    assert len(window.visible_names()) == 6
    assert window.update_args() == ()                 # filtering never changes the selection


def test_reason_line_refresh_keeps_choices_and_up_to_date(window):
    window.set_checked(("aur", "yay-bin"), False)
    window.set_pending(_pending(ITEMS + ({"name": "new-one", "old": "1", "new": "2", "source": "aur"},),
                                reason="Arch news reports manual intervention for: linux"))
    assert window.reason_label.text() == "⚠ Arch news reports manual intervention for: linux"
    assert not window.reason_label.isHidden()
    assert ("aur", "yay-bin") not in window.selected() and ("aur", "new-one") in window.selected()
    window.set_pending(None, error="checkupdates failed")
    assert window.summary.startswith("Update check failed") and len(window.items) == 7
    window.set_pending(None)
    assert window.summary == "System is up to date" and window.repo_note.isHidden()
    assert not window.update_button.isEnabled()


def test_pending_dialog_update_card_only_opens_updates(qapp):
    from arch_assistant.tray import ItemsWindow, PendingDialog
    upd = _pending()
    other = C.Pending(Finding("orphans", "low", "2 orphans", "S", ({"name": "a"},), ()), Action("remove-orphans", "Remove"))
    opened, ran = [], []
    dlg = PendingDialog([upd, other], on_run=lambda p, a: ran.append(p), on_decline=lambda p: None,
                        on_updates=opened.append)
    row = dlg.rows[upd.key]
    assert "details" not in row and row["run"].text() == "Show updates" and row["auto"].isHidden()
    row["run"].click()
    assert opened == [upd] and ran == [] and row["busy"].isHidden()
    dlg.rows[other.key]["details"].click()
    assert isinstance(dlg.details_window, ItemsWindow)
    dlg.details_window.close()
    dlg.deleteLater()
    qapp.processEvents()


# --- tray wiring -------------------------------------------------------------------------------

class FakeTransport:
    def __init__(self):
        self.calls = []

    def notify(self, *args):
        self.calls.append(args)
        return len(self.calls)

    def listen(self, on_action, on_closed):
        pass


def _wait(qapp, cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def _updates_check(d, items):
    data = {"id": "updates", "severity": "low", "title": f"{len(items)} pending updates", "summary": "S",
            "items": list(items), "actions": [{"id": "update", "label": "Update system", "args": []}]}
    p = d / "updates.sh"
    p.write_text(f"#!/bin/bash\necho '{json.dumps(data)}'\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def tray(qapp, tmp_path, monkeypatch):
    from arch_assistant.notify import Notifier
    from arch_assistant.tray import TrayApp
    cfg = load_config(tmp_path / "cfg", tmp_path / "state")
    cfg.checks_dir, cfg.actions_dir = tmp_path / "checks", tmp_path / "actions"
    cfg.checks_dir.mkdir()
    cfg.actions_dir.mkdir()
    monkeypatch.setattr(C, "template_summary", lambda findings: Summary("H", "B", []))
    transport = FakeTransport()
    app = TrayApp(cfg, State(tmp_path / "state" / "state.json"), LOG, notifier=Notifier(transport=transport))
    app.timer.stop()
    app.transport = transport
    yield app
    if app.packages_window is not None:
        app.packages_window.close()
    app.tray.hide()
    app.tray.setContextMenu(None)
    app.menu.deleteLater()
    app.tray.deleteLater()
    app.deleteLater()
    qapp.processEvents()


def test_menu_always_has_packages(tray):
    assert "Packages…" in [a.text() for a in tray.menu.actions()]


def test_refresh_replaces_updates_pending_without_notifying(qapp, tray):
    _updates_check(tray.cfg.checks_dir, ITEMS[:2])
    news = Finding("arch-news", "none", "No news", "")
    other = C.Pending(Finding("orphans", "low", "T", "S"), Action("remove-orphans", "Remove"))
    stale = _pending(ITEMS[:1])
    tray.findings, tray.pending = [news, stale.finding], [stale, other]
    tray.show_updates()
    tray.refresh_updates()
    assert tray.busy and tray.packages_window.updates_tab.running
    assert _wait(qapp, lambda: not tray.busy)
    fresh = tray.pending[0]
    assert [i["name"] for i in fresh.finding.items] == ["vim", "linux"] and tray.pending[1] is other
    assert fresh.reason is None                         # news present, no VM packages: no confirmation needed
    assert tray.updates is fresh and tray.packages_window.updates_tab.summary == "2 updates available"
    assert not tray.packages_window.updates_tab.running
    assert tray.transport.calls == []


def test_refresh_without_arch_news_keeps_the_guard(qapp, tray):
    _updates_check(tray.cfg.checks_dir, ITEMS[:1])
    tray.refresh_updates()
    assert _wait(qapp, lambda: not tray.busy)
    assert tray.updates.reason.startswith("Arch news check unavailable")


def test_refresh_respects_busy(tray):
    tray.busy = True
    tray.refresh_updates()
    assert tray.busy and tray.updates is None


def test_update_selected_goes_through_run_pending_and_cleans_up(qapp, tray, monkeypatch):
    tray.updates, tray.updates_loaded = _pending(reason="careful"), True
    tray.pending = [tray.updates, C.Pending(Finding("arch-news", "low", "T", "S"), Action("update", "Update system"))]
    tray.show_updates()
    ran = []
    monkeypatch.setattr(tray, "run_pending", lambda p, auto=False: ran.append(p))
    tray._update_selected(("repo", "aur:mbpoll-git"))
    assert ran[0].action == Action("update", "Update system", ("repo", "aur:mbpoll-git")) and ran[0].reason == "careful"
    assert tray.packages_window.updates_tab.running
    refreshed = []
    monkeypatch.setattr(tray, "refresh_updates", lambda: refreshed.append(True))
    tray._on_action_finished(ran[0], ActionResult("update", True, "Updated", "", 1.0), [])
    assert tray.pending == [] and refreshed == [True]


def test_menu_entry_and_notification_of_update_open_the_window(tray, monkeypatch):
    from arch_assistant.tray import BUTTONS_UPDATES
    p = _pending()
    opened, ran = [], []
    monkeypatch.setattr(tray, "show_updates", lambda: opened.append(True))
    monkeypatch.setattr(tray, "run_pending", lambda q, auto=False: ran.append(q))
    tray.pending = [p]
    tray._rebuild_menu()
    next(a for a in tray.menu.actions() if a.text().startswith("Update system")).trigger()
    assert opened == [True] and ran == []
    tray._notify_summary(Summary("H", "B", []), [p], [p.finding])
    sent = str(tray.transport.calls[-1])
    assert all(label in sent for _key, label in BUTTONS_UPDATES) and "Yes, automatically" not in sent
    tray._on_button("updates", p)
    assert opened == [True, True] and ran == []
