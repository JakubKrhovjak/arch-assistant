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
                   (Action("update", "Update system"),))


def test_report_first_time_then_not_again(state, cfg):
    f = finding(items=[{"name": "a"}])
    assert state.should_report(f, cfg, NOW)
    state.mark_reported(f, NOW)
    assert not state.should_report(f, cfg, NOW + timedelta(hours=6))


def test_report_again_after_7_days(state, cfg):
    f = finding()
    state.mark_reported(f, NOW)
    assert state.should_report(f, cfg, NOW + timedelta(days=7))


def test_decline_snoozes_for_snooze_days(state, cfg):
    f = finding()
    state.mark_reported(f, NOW)
    state.mark_declined(f, NOW, cfg)
    assert state.is_snoozed(f, NOW + timedelta(days=6))
    assert not state.should_report(f, cfg, NOW + timedelta(days=6))
    assert not state.is_snoozed(f, NOW + timedelta(days=7))
    assert state.should_report(f, cfg, NOW + timedelta(days=7))


def test_snooze_silences_a_changed_finding_too(state, cfg):
    f = finding(items=[{"name": "a"}])
    state.mark_reported(f, NOW)
    state.mark_declined(f, NOW, cfg)
    g = finding(items=[{"name": "a"}, {"name": "b"}])
    assert not state.should_report(g, cfg, NOW + timedelta(days=1))


def test_snooze_applies_to_updates(state, cfg):
    f = updates("linux")
    state.mark_reported(f, NOW)
    state.mark_declined(f, NOW, cfg)
    assert not state.should_report(updates("linux", "plasma-desktop"), cfg, NOW + timedelta(days=2))
    assert state.should_report(updates("linux", "plasma-desktop"), cfg, NOW + timedelta(days=7))


def test_critical_finding_ignores_snooze(state, cfg):
    f = finding(severity="critical")
    state.mark_reported(f, NOW)
    state.mark_declined(f, NOW, cfg)
    assert not state.is_snoozed(f, NOW + timedelta(days=1))
    g = finding(severity="critical", items=[{"name": "x"}])
    assert state.should_report(g, cfg, NOW + timedelta(days=1))


def test_reminder_after_snooze_is_sent_once(state, cfg):
    cfg.thresholds["snooze_days"] = 3   # shorter than report_days: the reminder still comes on day 3
    f = finding()
    state.mark_reported(f, NOW)
    state.mark_declined(f, NOW, cfg)
    assert state.should_report(f, cfg, NOW + timedelta(days=3))
    state.mark_reported(f, NOW + timedelta(days=3))
    assert not state.should_report(f, cfg, NOW + timedelta(days=4))


def test_old_string_decline_entries_are_not_snoozed(state, cfg):
    f = finding()
    state.data["declined"]["orphans"] = "deadbeef"   # format before snoozing existed
    assert not state.is_snoozed(f, NOW)


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
