"""Tests for the final-review fix pass (robustness, locking, pending queue, dedup, prompt hygiene, tray)."""
import json
import logging
import os
import stat
import threading
import time
from datetime import datetime, timedelta

import pytest

from arch_assistant import actions as A
from arch_assistant import cycle as C
from arch_assistant import summarizer as S
from arch_assistant.config import load_config
from arch_assistant.model import Action, Finding, Summary
from arch_assistant.state import State

LOG = logging.getLogger("test")
NOW = datetime(2026, 10, 17, 12, 0)


# --- 1. bad check output never escapes as an exception --------------------------------------

@pytest.mark.parametrize("text", [
    '{"id":"x","severity":"low","title":"T","items":[5]}',
    '{"id":"x","severity":"low","title":"T","actions":[{"label":"no id"}]}',
    '{"id":"x","severity":"low","title":"T","actions":"nope"}',
    '[1,2]',
])
def test_from_json_rejects_bad_shapes_with_value_error(text):
    with pytest.raises(ValueError):
        Finding.from_json(text)


def test_from_json_accepts_null_items_as_empty():
    f = Finding.from_json('{"id":"x","severity":"low","title":"T","items":null,"actions":null}')
    assert f.items == () and f.actions == ()


def test_run_action_non_executable_script_is_failure(tmp_path):
    p = tmp_path / "noexec.sh"
    p.write_text("#!/bin/bash\necho hi\n")
    p.chmod(0o644)
    r = A.run_action(tmp_path, Action("noexec", "No exec"), LOG)
    assert not r.ok and "cannot start" in r.output


# --- 2. state writes are safe under concurrency -----------------------------------------------

def test_state_save_is_thread_safe(tmp_path):
    s = State(tmp_path / "state.json")
    errors = []

    def worker(i):
        try:
            for _ in range(30):
                s.data["reported"][f"k{i}"] = {"fp": str(i), "at": NOW.isoformat()}
                s.save()
        except Exception as e:  # pragma: no cover - the assertion below reports it
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert json.loads((tmp_path / "state.json").read_text())["reported"]


def _env(tmp_path, monkeypatch):
    cfg = load_config(tmp_path / "cfg", tmp_path / "state")
    cfg.checks_dir = tmp_path / "checks"
    cfg.actions_dir = tmp_path / "actions"
    cfg.checks_dir.mkdir()
    cfg.actions_dir.mkdir()
    monkeypatch.setattr(C, "summarize", lambda findings, news, cfg, key, log: Summary("H", "B", []))
    return cfg, State(tmp_path / "state" / "state.json")


def _check(d, cid, severity="low", items=(), actions=(), error=False):
    data = {"id": cid, "severity": severity, "title": f"T {cid}", "summary": "S", "items": list(items),
            "actions": [{"id": a, "label": a, "args": []} for a in actions]}
    p = d / f"{cid}.sh"
    p.write_text("#!/bin/bash\n" + ("exit 3\n" if error else f"echo '{json.dumps(data)}'\n"))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def test_decline_waits_for_the_cycle_lock(tmp_path, monkeypatch):
    cfg, state = _env(tmp_path, monkeypatch)
    c = C.Cycle(cfg, state, LOG, None, now=lambda: NOW)
    p = C.Pending(Finding("orphans", "low", "T", "S", (), (Action("remove-orphans", "R"),)), Action("remove-orphans", "R"))
    c._lock.acquire()
    done = threading.Event()
    threading.Thread(target=lambda: (c.decline(p), done.set())).start()
    assert not done.wait(0.3)
    c._lock.release()
    assert done.wait(2)


# --- 3. pending queue reflects the current findings, not only the new ones --------------------

def test_pending_is_rebuilt_from_all_current_findings(tmp_path, monkeypatch):
    cfg, state = _env(tmp_path, monkeypatch)
    _check(cfg.checks_dir, "orphans", items=[{"name": "a"}], actions=["remove-orphans"])
    c = C.Cycle(cfg, state, LOG, None, now=lambda: NOW)
    first = c.run()
    assert len(first.pending) == 1 and len(first.new_pending) == 1
    second = c.run()
    assert second.summary is None and second.new_pending == []
    assert [p.action.id for p in second.pending] == ["remove-orphans"]


def test_declined_finding_is_not_pending(tmp_path, monkeypatch):
    cfg, state = _env(tmp_path, monkeypatch)
    _check(cfg.checks_dir, "orphans", items=[{"name": "a"}], actions=["remove-orphans"])
    c = C.Cycle(cfg, state, LOG, None, now=lambda: NOW)
    p = c.run().pending[0]
    c.decline(p)
    assert c.run().pending == []


def test_never_mode_is_not_pending(tmp_path, monkeypatch):
    cfg, state = _env(tmp_path, monkeypatch)
    cfg.actions["remove-orphans"] = "never"
    _check(cfg.checks_dir, "orphans", items=[{"name": "a"}], actions=["remove-orphans"])
    assert C.Cycle(cfg, state, LOG, None, now=lambda: NOW).run().pending == []


# --- 4. updates rule: versions matter and a completed update resets the record ------------------

def _updates(**versions):
    return Finding("updates", "high", "U", "", tuple({"name": n, "new": v} for n, v in versions.items()),
                   (Action("update", "Update system"),))


def test_updates_new_kernel_version_is_reported_again(tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    s = State(tmp_path / "state.json")
    s.mark_reported(_updates(linux="7.2.10-arch1-1"), NOW)
    assert not s.should_report(_updates(linux="7.2.10-arch1-1"), cfg, NOW + timedelta(days=1))
    assert s.should_report(_updates(linux="7.2.11-arch1-1"), cfg, NOW + timedelta(days=1))


def test_updates_count_rule_triggers_on_a_different_batch(tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    s = State(tmp_path / "state.json")
    s.mark_reported(_updates(**{f"p{i}": "1" for i in range(25)}), NOW)
    assert not s.should_report(_updates(**{f"p{i}": "1" for i in range(25)}), cfg, NOW + timedelta(days=1))
    assert s.should_report(_updates(**{f"q{i}": "1" for i in range(22)}), cfg, NOW + timedelta(days=1))
    assert not s.should_report(_updates(**{f"p{i}": "1" for i in range(12)}), cfg, NOW + timedelta(days=1))


def test_update_run_resets_the_report_record(tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    s = State(tmp_path / "state.json")
    s.mark_reported(_updates(linux="7.2.10-arch1-1"), NOW)
    s.mark_update_run(NOW + timedelta(hours=1))
    assert s.should_report(_updates(linux="7.2.10-arch1-1"), cfg, NOW + timedelta(hours=2))


# --- 6. safety guards do not pass on their own failure --------------------------------------

def test_needs_confirmation_when_arch_news_check_failed():
    f = _updates(linux="1")
    news = Finding.failed("arch-news", "timeout")
    reason = A.needs_confirmation(f.actions[0], f, [f, news], running_vms=lambda: [])
    assert reason and "Arch news" in reason


def test_needs_confirmation_when_arch_news_missing():
    f = _updates(linux="1")
    assert A.needs_confirmation(f.actions[0], f, [f], running_vms=lambda: [])


def test_needs_confirmation_when_vm_state_unknown():
    f = _updates(**{"qemu-desktop": "1"})
    news = Finding("arch-news", "none", "", "")
    assert A.needs_confirmation(f.actions[0], f, [f, news], running_vms=lambda: None)
    assert A.needs_confirmation(f.actions[0], f, [f, news], running_vms=lambda: []) is None


def test_running_vms_returns_none_on_failure(shim):
    shim.cmd("virsh", "", exit_code=1, stderr="error: failed to connect")
    assert A.running_vms() is None


# --- 7. nothing from /home reaches the model -------------------------------------------------

def test_prompt_strips_paths_and_home_strings():
    f = Finding("jetbrains-old", "low", "Old", "S",
                ({"name": "GoLand2025.2", "size_mb": 3, "paths": ["/home/redo/.config/JetBrains/GoLand2025.2"]},
                 {"name": "/home/redo/.cache/foo", "size_mb": 1}), ())
    prompt = S.build_prompt([f], "")
    assert "/home/" not in prompt and "GoLand2025.2" in prompt


# --- tray: worker exceptions, pending replacement, dialog, icon ------------------------------

@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


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


@pytest.fixture
def tray_factory(qapp, tmp_path, monkeypatch):
    from arch_assistant.notify import Notifier
    from arch_assistant.tray import TrayApp
    apps = []

    def make():
        cfg, state = _env(tmp_path, monkeypatch)
        transport = FakeTransport()
        app = TrayApp(cfg, state, LOG, None, notifier=Notifier(transport=transport))
        app.timer.stop()
        apps.append(app)
        return app, transport

    yield make
    for app in apps:   # tear Qt objects down explicitly so interpreter exit does not race their destructors
        if app.dialog is not None:
            app.dialog.close()
        app.tray.hide()
        app.tray.setContextMenu(None)
        app.menu.deleteLater()
        app.tray.deleteLater()
        app.deleteLater()
    qapp.processEvents()


def test_tray_recovers_from_a_crashing_cycle(qapp, tray_factory, monkeypatch):
    app, _ = tray_factory()
    monkeypatch.setattr(app.cycle, "run", lambda only=None: (_ for _ in ()).throw(RuntimeError("boom")))
    app.check_now()
    assert _wait(qapp, lambda: not app.busy)


def test_tray_recovers_from_a_crashing_action(qapp, tray_factory, monkeypatch):
    app, transport = tray_factory()
    p = C.Pending(Finding("orphans", "low", "T", "S", (), ()), Action("remove-orphans", "Remove"))
    monkeypatch.setattr(app.cycle, "run_pending", lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    app.run_pending(p)
    assert _wait(qapp, lambda: not app.busy)
    assert transport.calls and "failed" in transport.calls[-1][3]


def test_tray_replaces_pending_list_each_cycle(qapp, tray_factory, monkeypatch):
    app, _ = tray_factory()
    stale = C.Pending(Finding("old", "low", "T", "S", (), ()), Action("x", "X"))
    app.pending = [stale]
    fresh = C.Pending(Finding("orphans", "low", "T", "S", (), ()), Action("remove-orphans", "Remove"))
    app._on_cycle_finished(C.CycleResult([fresh.finding], [], None, pending=[fresh], new_pending=[]))
    assert app.pending == [fresh]


def test_tray_notification_uses_icon_file(qapp, tray_factory, monkeypatch):
    app, transport = tray_factory()
    app._on_cycle_finished(C.CycleResult([], [Finding("x", "low", "T", "S")], Summary("H", "B", []), pending=[], new_pending=[]))
    icon = transport.calls[-1][2]
    assert icon.endswith("assets/tray.svg") and os.path.isfile(icon)


def test_pending_dialog_lists_and_runs(qapp):
    from arch_assistant.tray import PendingDialog
    p = C.Pending(Finding("orphans", "low", "3 orphans", "S", (), ()), Action("remove-orphans", "Remove orphans"), "needs care")
    ran, declined = [], []
    dlg = PendingDialog([p], on_run=lambda p, auto: ran.append((p, auto)), on_decline=declined.append)
    texts = [b.text() for b in dlg.run_buttons]
    assert texts == ["Run"] and "Remove orphans: 3 orphans" in dlg.labels[0].text() and "needs care" in dlg.labels[0].text()
    assert dlg.auto_boxes[0].text() == "Next time run automatically, just notify" and not dlg.auto_boxes[0].isChecked()
    dlg.auto_boxes[0].setChecked(True)
    dlg.run_buttons[0].click()
    assert ran == [(p, True)]
    dlg.run_buttons[0].click()           # the row is locked while the action runs
    assert ran == [(p, True)]
    dlg.finish_row(p, ok=False)          # failure unlocks it again
    dlg.decline_buttons[0].click()
    assert declined == [p]
    dlg.deleteLater()
    qapp.processEvents()


def test_tray_run_pending_with_auto_sets_mode(qapp, tray_factory, monkeypatch):
    app, _ = tray_factory()
    p = C.Pending(Finding("orphans", "low", "T", "S", (), ()), Action("remove-orphans", "Remove"))
    monkeypatch.setattr(app.cycle, "run_pending", lambda p: (__import__("arch_assistant.model", fromlist=["ActionResult"]).ActionResult("remove-orphans", True, "ok", "", 0.1), []))
    app.run_pending(p, auto=True)
    assert _wait(qapp, lambda: not app.busy)
    assert app.cfg.actions["remove-orphans"] == "auto"
    assert (app.cfg.config_dir / "actions.toml").read_text().count('remove-orphans = "auto"') == 1
