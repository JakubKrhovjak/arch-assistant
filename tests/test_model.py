import pytest

from arch_assistant.model import Action, Finding


def test_from_json_parses_contract():
    f = Finding.from_json(
        '{"id":"x","severity":"low","title":"T","summary":"S",'
        '"items":[{"name":"a","size_mb":1}],'
        '"actions":[{"id":"clean","label":"Smazat","args":["a"]}]}'
    )
    assert f.actions[0] == Action("clean", "Smazat", ("a",))
    assert f.items[0]["size_mb"] == 1
    assert (f.id, f.severity, f.title, f.summary) == ("x", "low", "T", "S")


def test_from_json_rejects_unknown_severity():
    with pytest.raises(ValueError):
        Finding.from_json('{"id":"x","severity":"huge","title":"T"}')


def test_from_json_rejects_missing_id():
    with pytest.raises(ValueError):
        Finding.from_json('{"severity":"low","title":"T"}')


def test_failed_finding():
    f = Finding.failed("updates", "boom\nmore")
    assert (f.severity, f.title, f.summary, f.error) == (
        "low", "Check updates failed", "boom", "boom\nmore")


def test_is_empty():
    assert Finding("x", "none", "", "").is_empty
    assert not Finding("x", "low", "", "").is_empty
