import json
import logging
import stat
import threading
import time
from datetime import datetime

import pytest

from arch_assistant import cycle as C
from arch_assistant.config import load_config
from arch_assistant.model import Summary
from arch_assistant.state import State

LOG = logging.getLogger("test")
NOW = datetime(2026, 10, 17, 12, 0)


def _script(d, name, body):
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _check(d, cid, severity="low", items=(), actions=(), extra=""):
    data = {"id": cid, "severity": severity, "title": f"T {cid}", "summary": "S", "items": list(items),
            "actions": [{"id": a, "label": a, "args": []} for a in actions]}
    _script(d, f"{cid}.sh", extra + f"echo '{json.dumps(data)}'\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = load_config(tmp_path / "cfg", tmp_path / "state")
    cfg.checks_dir = tmp_path / "checks"
    cfg.actions_dir = tmp_path / "actions"
    cfg.checks_dir.mkdir()
    cfg.actions_dir.mkdir()
    monkeypatch.setattr(C, "template_summary", lambda findings: Summary("H", "B", []))
    state = State(tmp_path / "state" / "state.json")
    return cfg, state


def _cycle(env, running_vms=lambda: [], now=NOW):
    cfg, state = env
    return C.Cycle(cfg, state, LOG, running_vms=running_vms, now=lambda: now)


def test_cycle_quiet_when_nothing_new(env):
    _check(env[0].checks_dir, "orphans", severity="none")
    r = _cycle(env).run()
    assert r.summary is None and r.auto_run == [] and r.pending == []
    assert [f.id for f in r.findings] == ["orphans"]


def test_cycle_ask_creates_pending(env):
    _check(env[0].checks_dir, "orphans", items=[{"name": "a"}], actions=["remove-orphans"])
    r = _cycle(env).run()
    assert r.summary == Summary("H", "B", [])
    assert len(r.pending) == 1 and r.pending[0].action.id == "remove-orphans" and r.pending[0].reason is None
    assert r.auto_run == []


def test_cycle_update_never_auto_post_hooks_run_on_click(env):
    cfg, state = env
    cfg.actions["update"] = "auto"   # capped at ask: the update only runs from the Updates window
    _check(cfg.checks_dir, "arch-news", severity="none")
    _check(cfg.checks_dir, "updates", "high", items=[{"name": "linux"}], actions=["update"])
    _check(cfg.checks_dir, "secure-boot", severity="none")
    _check(cfg.checks_dir, "reboot-needed", severity="medium")
    _script(cfg.actions_dir, "update.sh", "echo 'Updated 1 packages (repo) and 0 (AUR)'\n")
    cyc = _cycle(env)
    r = cyc.run()
    assert r.auto_run == [] and [p.action.id for p in r.pending] == ["update"]
    res, extra = cyc.run_pending(r.pending[0])
    assert res.ok
    assert [f.id for f in extra] == ["secure-boot", "reboot-needed"] or sorted(f.id for f in extra) == ["reboot-needed", "secure-boot"]
    assert state.data["last_update_run"] == NOW.isoformat()


def test_cycle_post_hooks_run_even_when_action_fails(env):
    cfg, state = env
    _check(cfg.checks_dir, "arch-news", severity="none")
    _check(cfg.checks_dir, "updates", "high", items=[{"name": "linux"}], actions=["update"])
    _check(cfg.checks_dir, "secure-boot", severity="critical")
    _check(cfg.checks_dir, "reboot-needed", severity="none")
    _script(cfg.actions_dir, "update.sh", "echo 'Update failed: boom'\nexit 1\n")
    cyc = _cycle(env)
    r = cyc.run()
    res, extra = cyc.run_pending(r.pending[0])
    assert res.ok is False
    assert any(f.id == "secure-boot" and f.severity == "critical" for f in extra)
    assert any(f.id == "reboot-needed" for f in extra)


def test_cycle_auto_blocked_by_reason(env):
    cfg, state = env
    cfg.actions["update"] = "auto"
    _check(cfg.checks_dir, "arch-news", severity="none")
    _check(cfg.checks_dir, "updates", "high", items=[{"name": "qemu-desktop"}], actions=["update"])
    _script(cfg.actions_dir, "update.sh", "echo ran > ran.txt\n")
    r = _cycle(env, running_vms=lambda: ["win11-tia"]).run()
    assert r.auto_run == []
    assert len(r.pending) == 1 and "win11-tia" in r.pending[0].reason


def test_cycle_never_mode_only_marks_reported(env):
    cfg, state = env
    cfg.actions["remove-orphans"] = "never"
    _check(cfg.checks_dir, "orphans", items=[{"name": "a"}], actions=["remove-orphans"])
    r = _cycle(env).run()
    assert r.pending == [] and r.auto_run == [] and r.summary is not None
    assert "orphans" in state.data["reported"]


def test_cycle_marks_reported_so_second_run_is_quiet(env):
    _check(env[0].checks_dir, "orphans", items=[{"name": "a"}], actions=["remove-orphans"])
    c = _cycle(env)
    assert c.run().summary is not None
    assert c.run().summary is None


def test_cycle_run_is_rejected_while_running(env):
    cfg, state = env
    _check(cfg.checks_dir, "slow", extra="sleep 1\n")
    c = _cycle(env)
    results = {}
    t = threading.Thread(target=lambda: results.setdefault("first", c.run()))
    t.start()
    time.sleep(0.3)
    assert c.run() is None
    t.join()
    assert results["first"] is not None


def test_run_pending_executes_and_marks(env):
    cfg, state = env
    _check(cfg.checks_dir, "orphans", items=[{"name": "a"}], actions=["remove-orphans"])
    _script(cfg.actions_dir, "remove-orphans.sh", "echo removed\n")
    c = _cycle(env)
    p = c.run().pending[0]
    res, extra = c.run_pending(p)
    assert res.ok and res.output == "removed" and extra == []


def _runner_calls(monkeypatch, available=True):
    """Records which runner run_pending picks: the terminal one or the background one."""
    calls = []

    def fake(name):
        def run(actions_dir, action, log):
            calls.append((name, action.id))
            return C.ActionResult(action.id, True, "ok", "", 0.0)
        return run

    monkeypatch.setattr(C, "run_action", fake("background"))
    monkeypatch.setattr(C, "run_action_in_terminal", fake("terminal"))
    monkeypatch.setattr(C, "terminal_available", lambda: available)
    return calls


def test_run_pending_long_action_runs_in_terminal(env, monkeypatch):
    cfg, _ = env
    _check(cfg.checks_dir, "orphans", items=[{"name": "a"}], actions=["remove-orphans"])
    c = _cycle(env)
    p = c.run().pending[0]
    calls = _runner_calls(monkeypatch)
    c.run_pending(p)
    assert calls == [("terminal", "remove-orphans")]


@pytest.mark.parametrize("progress, available, action", [
    (False, True, "remove-orphans"),   # turned off in config.toml
    (True, False, "remove-orphans"),   # no terminal or no graphical session
    (True, True, "restart-unit"),      # a short action
])
def test_run_pending_falls_back_to_background(env, monkeypatch, progress, available, action):
    cfg, _ = env
    cfg.terminal_progress = progress
    _check(cfg.checks_dir, "x", items=[{"name": "a"}], actions=[action])
    c = _cycle(env)
    p = c.run().pending[0]
    calls = _runner_calls(monkeypatch, available)
    c.run_pending(p)
    assert calls == [("background", action)]


def test_cycle_saves_state_file(env):
    cfg, state = env
    _check(cfg.checks_dir, "orphans", severity="none")
    _cycle(env).run()
    assert state.path.is_file()
    assert json.loads(state.path.read_text())["last_findings"][0]["id"] == "orphans"
