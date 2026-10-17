from datetime import datetime, timedelta

import pytest

from arch_assistant.config import load_config
from arch_assistant.model import Action, Finding
from arch_assistant.state import State, fingerprint

NOW = datetime(2026, 10, 17, 12, 0)


@pytest.fixture
def cfg(tmp_path):
    return load_config(tmp_path, tmp_path)


@pytest.fixture
def state(tmp_path):
    return State(tmp_path / "state.json")


def finding(id="orphans", items=(), severity="low", summary="S"):
    return Finding(id, severity, "T", summary, tuple(items), (Action("x", "X"),))


def updates(*names):
    return Finding("updates", "low", "Aktualizace", "", tuple({"name": n} for n in names),
                   (Action("update", "Aktualizovat systém"),))


def test_report_first_time_then_not_again(state, cfg):
    f = finding(items=[{"name": "a"}])
    assert state.should_report(f, cfg, NOW)
    state.mark_reported(f, NOW)
    assert not state.should_report(f, cfg, NOW + timedelta(hours=6))


def test_report_again_after_7_days(state, cfg):
    f = finding()
    state.mark_reported(f, NOW)
    assert state.should_report(f, cfg, NOW + timedelta(days=7))


def test_declined_same_fingerprint_not_reported(state, cfg):
    f = finding()
    state.mark_reported(f, NOW)
    state.mark_declined(f)
    assert not state.should_report(f, cfg, NOW + timedelta(days=30))


def test_changed_fingerprint_reported_even_if_declined(state, cfg):
    f = finding(items=[{"name": "a"}])
    state.mark_reported(f, NOW)
    state.mark_declined(f)
    g = finding(items=[{"name": "a"}, {"name": "b"}])
    assert fingerprint(f) != fingerprint(g)
    assert state.should_report(g, cfg, NOW)


def test_fingerprint_ignores_sizes(state):
    assert fingerprint(finding(items=[{"name": "a", "size_mb": 1}])) == fingerprint(finding(items=[{"name": "a", "size_mb": 999}]))


def test_empty_finding_never_reported(state, cfg):
    assert not state.should_report(Finding("x", "none", "", ""), cfg, NOW)


def test_updates_not_reported_below_20_without_critical(state, cfg):
    assert not state.should_report(updates(*[f"pkg{i}" for i in range(12)]), cfg, NOW)


def test_updates_reported_when_critical_appears(state, cfg):
    f = updates("vim", "linux")
    assert state.should_report(f, cfg, NOW)
    state.mark_reported(f, NOW)
    assert not state.should_report(f, cfg, NOW + timedelta(days=1))
    g = updates("vim", "linux", "plasma-desktop")
    assert state.should_report(g, cfg, NOW + timedelta(days=1))


def test_updates_reported_at_20(state, cfg):
    assert state.should_report(updates(*[f"pkg{i}" for i in range(20)]), cfg, NOW)


def test_updates_reported_after_7_days(state, cfg):
    f = updates("vim")
    state.mark_reported(updates("linux"), NOW)
    assert not state.should_report(f, cfg, NOW + timedelta(days=6))
    assert state.should_report(f, cfg, NOW + timedelta(days=7))


def test_new_findings_filters(state, cfg):
    a, b = finding("a", items=[{"name": "1"}]), finding("b")
    state.mark_reported(b, NOW)
    assert state.new_findings([a, b, Finding("c", "none", "", "")], cfg, NOW) == [a]


def test_save_and_load_roundtrip(tmp_path, cfg):
    s = State(tmp_path / "state.json")
    s.mark_reported(finding(), NOW)
    s.mark_update_run(NOW)
    s.save()
    t = State(tmp_path / "state.json")
    t.load()
    assert t.data["reported"]["orphans"]["at"] == NOW.isoformat()
    assert t.data["last_update_run"] == NOW.isoformat()
    assert not t.should_report(finding(), cfg, NOW)
