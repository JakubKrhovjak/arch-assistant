"""Dialog stays open while an action runs (busy row, row disappears on success); Details window."""
import logging
import os

import pytest

from arch_assistant import cycle as C
from arch_assistant.model import Action, Finding

LOG = logging.getLogger("test")


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pending(fid="orphans", aid="remove-orphans"):
    return C.Pending(Finding(fid, "low", f"T {fid}", "S", (), ()), Action(aid, f"Do {aid}"))


# --- dialog behaviour --------------------------------------------------------------------------

def test_dialog_run_keeps_window_open_and_marks_row_busy(qapp):
    from arch_assistant.tray import PendingDialog
    p = _pending()
    ran = []
    dlg = PendingDialog([p], on_run=lambda p, auto: ran.append(p), on_decline=lambda p: None)
    dlg.show()
    dlg.run_buttons[0].click()
    qapp.processEvents()
    assert ran == [p]
    assert dlg.isVisible()
    row = dlg.rows[p.key]
    assert row["busy"].isVisible() or not row["busy"].isHidden()
    assert not row["run"].isEnabled() and not row["no"].isEnabled()
    dlg.close()
    dlg.deleteLater()
    qapp.processEvents()


def test_dialog_finish_row_success_removes_row_failure_reenables(qapp):
    from arch_assistant.tray import PendingDialog
    a, b = _pending("orphans"), _pending("coredumps", "clean-coredumps")
    dlg = PendingDialog([a, b], on_run=lambda p, auto: None, on_decline=lambda p: None)
    dlg.set_running(a)
    dlg.set_running(b)
    dlg.finish_row(a, ok=True)
    assert a.key not in dlg.rows and b.key in dlg.rows
    dlg.finish_row(b, ok=False)
    row = dlg.rows[b.key]
    assert row["run"].isEnabled() and row["no"].isEnabled() and row["busy"].isHidden()
    dlg.finish_row(_pending("nope"), ok=True)   # unknown row is ignored
    dlg.deleteLater()
    qapp.processEvents()


def test_dialog_decline_removes_row_only(qapp):
    from arch_assistant.tray import PendingDialog
    a, b = _pending("orphans"), _pending("coredumps", "clean-coredumps")
    declined = []
    dlg = PendingDialog([a, b], on_run=lambda p, auto: None, on_decline=declined.append)
    dlg.decline_buttons[0].click()
    assert declined == [a] and a.key not in dlg.rows and b.key in dlg.rows
    dlg.deleteLater()
    qapp.processEvents()


UPDATE_ITEMS = (
    {"name": "zlib", "old": "1.3-1", "new": "1.3-2", "source": "repo", "critical": False},
    {"name": "kwin", "old": "6.8.2-1", "new": "6.8.2-2", "source": "repo", "critical": True},
    {"name": "com.google.Chrome", "old": "", "new": "156.0.8078.79", "source": "flatpak", "critical": False},
)


def test_dialog_details_button_only_for_findings_with_items(qapp):
    from arch_assistant.tray import PendingDialog
    plain = _pending()
    updates = C.Pending(Finding("updates", "high", "3 pending updates", "S", UPDATE_ITEMS, ()), Action("update", "Update system"))
    dlg = PendingDialog([plain, updates], on_run=lambda p, auto: None, on_decline=lambda p: None)
    assert "details" not in dlg.rows[plain.key]
    assert len(dlg.details_buttons) == 1
    dlg.details_buttons[0].click()
    qapp.processEvents()
    text = dlg.details_window.text.toPlainText()
    lines = text.splitlines()
    assert "3 pending updates" in dlg.details_window.windowTitle()
    assert "kwin" in lines[0] and "6.8.2-1 → 6.8.2-2" in lines[0] and "⚠" in lines[0]  # critical first
    assert any("com.google.Chrome" in l and "? → 156.0.8078.79" in l and "[flatpak]" in l for l in lines)
    assert any("zlib" in l and "[repo]" in l for l in lines)
    dlg.details_window.close()
    dlg.deleteLater()
    qapp.processEvents()


def test_format_item_falls_back_to_key_values_for_other_items():
    from arch_assistant.tray import format_item
    assert format_item({"path": "/var/cache", "size": "2G"}) == "path=/var/cache, size=2G"
