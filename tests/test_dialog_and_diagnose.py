"""Dialog stays open while an action runs (busy row, row disappears on success); Diagnose feature."""
import json
import logging
import os
import stat
import time
from types import SimpleNamespace

import pytest

from arch_assistant import cycle as C
from arch_assistant import diagnose as D
from arch_assistant.config import load_config
from arch_assistant.model import Action, ActionResult, Finding, Summary

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
    dlg = PendingDialog([p], on_run=lambda p, auto: ran.append(p), on_decline=lambda p: None, on_diagnose=lambda p: None)
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
    dlg = PendingDialog([a, b], on_run=lambda p, auto: None, on_decline=lambda p: None, on_diagnose=lambda p: None)
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
    dlg = PendingDialog([a, b], on_run=lambda p, auto: None, on_decline=declined.append, on_diagnose=lambda p: None)
    dlg.decline_buttons[0].click()
    assert declined == [a] and a.key not in dlg.rows and b.key in dlg.rows
    dlg.deleteLater()
    qapp.processEvents()


def test_dialog_diagnose_button_calls_back(qapp):
    from arch_assistant.tray import PendingDialog
    p = _pending()
    asked = []
    dlg = PendingDialog([p], on_run=lambda p, auto: None, on_decline=lambda p: None, on_diagnose=asked.append)
    dlg.diagnose_buttons[0].click()
    assert asked == [p]
    dlg.deleteLater()
    qapp.processEvents()


# --- diagnose module ---------------------------------------------------------------------------

def test_collect_context_uses_script_when_present(tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    d = tmp_path / "diagnose"
    d.mkdir()
    s = d / "orphans.sh"
    s.write_text("#!/bin/bash\nread -r line\necho \"got: $line\"\necho \"AA_STATE_DIR=$AA_STATE_DIR\"\n")
    s.chmod(s.stat().st_mode | stat.S_IEXEC)
    f = Finding("orphans", "low", "T", "S", ({"name": "a"},), ())
    ctx = D.collect_context(f, cfg, diagnose_dir=d, log=LOG)
    assert ctx.startswith("got: {") and '"id": "orphans"' in ctx and f"AA_STATE_DIR={tmp_path}" in ctx


def test_collect_context_falls_back_to_finding_json(tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    f = Finding("orphans", "low", "T", "S", ({"name": "a"},), ())
    ctx = D.collect_context(f, cfg, diagnose_dir=tmp_path / "none", log=LOG)
    assert json.loads(ctx)["id"] == "orphans"


def test_collect_context_truncates_long_output(tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    d = tmp_path / "diagnose"
    d.mkdir()
    s = d / "orphans.sh"
    s.write_text("#!/bin/bash\nhead -c 100000 /dev/zero | tr '\\0' 'x'\n")
    s.chmod(s.stat().st_mode | stat.S_IEXEC)
    ctx = D.collect_context(Finding("orphans", "low", "T", "S"), cfg, diagnose_dir=d, log=LOG)
    assert len(ctx) <= D.MAX_CONTEXT_CHARS + 100 and ctx.endswith("[truncated]")


def test_diagnose_builds_prompt_and_parses_result(tmp_path, monkeypatch):
    cfg = load_config(tmp_path, tmp_path)
    captured = {}

    def fake_structured(prompt, model_cls, cfg, api_key, log):
        captured["prompt"] = prompt
        return model_cls(explanation="Unit X fails because Y.", commands=["systemctl --user restart x"], confidence="high")

    monkeypatch.setattr(D, "structured_call", fake_structured)
    f = Finding("failed-units", "high", "1 unit failed", "x.service (user, failed)", ({"name": "x.service"},), ())
    out = D.diagnose(f, cfg, None, LOG, context="● x.service failed\nJob failed")
    assert out.explanation.startswith("Unit X") and out.commands == ["systemctl --user restart x"]
    assert "Job failed" in captured["prompt"] and "1 unit failed" in captured["prompt"]


def test_diagnose_returns_none_when_model_unavailable(tmp_path, monkeypatch):
    cfg = load_config(tmp_path, tmp_path)
    monkeypatch.setattr(D, "structured_call", lambda *a, **k: None)
    assert D.diagnose(Finding("x", "low", "T", "S"), cfg, None, LOG, context="c") is None


def test_diagnose_script_failed_units(shim, tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    shim.cmd("systemctl", script='echo "STATUS $*"\n')
    shim.cmd("journalctl", script='echo "JOURNAL $*"\n')
    f = Finding("failed-units", "high", "T", "S",
                ({"name": "x.service", "scope": "user", "state": "failed", "restarts": 0, "load": "loaded"},
                 {"name": "y.service", "scope": "system", "state": "restarting", "restarts": 9, "load": "loaded"}), ())
    ctx = D.collect_context(f, cfg, diagnose_dir=D.DIAGNOSE_DIR, log=LOG)
    assert "STATUS --user status x.service" in ctx and "JOURNAL --user -u x.service" in ctx
    assert "STATUS status y.service" in ctx and "JOURNAL -u y.service" in ctx


def test_tray_diagnose_opens_window(qapp, tmp_path, monkeypatch):
    from arch_assistant.notify import Notifier
    from arch_assistant.tray import TrayApp

    class T:
        def notify(self, *a):
            return 1

        def listen(self, a, b):
            pass

    cfg = load_config(tmp_path / "cfg", tmp_path / "state")
    cfg.checks_dir, cfg.actions_dir = tmp_path / "c", tmp_path / "a"
    cfg.checks_dir.mkdir()
    cfg.actions_dir.mkdir()
    from arch_assistant import tray as TR
    monkeypatch.setattr(TR, "collect_context", lambda f, cfg, log: "ctx")
    monkeypatch.setattr(TR, "diagnose", lambda f, cfg, key, log, context: D.Diagnosis("Because.", ["echo fix"], "high"))
    app = TrayApp(cfg, TR.State(tmp_path / "state" / "state.json"), LOG, None, notifier=Notifier(transport=T()))
    app.timer.stop()
    p = _pending()
    app.diagnose_pending(p)
    end = time.monotonic() + 5
    while time.monotonic() < end and app.diagnosis_window is None:
        qapp.processEvents()
        time.sleep(0.02)
    assert app.diagnosis_window is not None
    assert "Because." in app.diagnosis_window.text.toPlainText() and "echo fix" in app.diagnosis_window.text.toPlainText()
    app.diagnosis_window.close()
    app.tray.hide()
    app.deleteLater()
    qapp.processEvents()
