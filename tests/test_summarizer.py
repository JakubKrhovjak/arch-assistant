import json
import logging
from types import SimpleNamespace

import pytest

from arch_assistant import summarizer as S
from arch_assistant.config import load_config
from arch_assistant.model import Action, Finding, Summary

LOG = logging.getLogger("test")


def f(id, severity="low", title="T", summary="S", n_items=0):
    return Finding(id, severity, title, summary, tuple({"name": f"i{i}", "size_mb": i} for i in range(n_items)),
                   (Action("x", "X"),))


def test_template_single_finding():
    s = S.template_summary([f("orphans", title="3 orphan packages", summary="a, b, c")])
    assert s.headline == "3 orphan packages"
    assert s.body == "• 3 orphan packages – a, b, c"
    assert s.risks == []


def test_template_many_findings_truncates_to_5():
    findings = [f(f"c{i}", "high" if i == 2 else "low", title=f"T{i}", summary=f"S{i}") for i in range(7)]
    s = S.template_summary(findings)
    assert s.headline == "7 things to deal with: Important"
    lines = s.body.splitlines()
    assert len(lines) == 6 and lines[0] == "• T0 – S0" and lines[-1] == "… and 2 more"


def test_build_prompt_collapses_long_items():
    prompt = S.build_prompt([f("jetbrains-old", n_items=12), f("orphans", n_items=3)], "news text")
    data = json.loads(prompt[prompt.index("{"):prompt.rindex("}") + 1]) if False else None  # prompt is prose + JSON
    assert '"count": 12' in prompt and '"i11"' not in prompt
    assert '"i2"' in prompt
    assert "news text" in prompt
    assert '"error"' not in prompt


def test_summarize_without_key_uses_template(tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    cfg.backend = "api"
    s = S.summarize([f("orphans", title="Orphans")], "", cfg, None, LOG)
    assert s.headline == "Orphans"


class FakeMessages:
    def __init__(self, parsed, stop_reason="end_turn", raise_exc=None):
        self.parsed, self.stop_reason, self.raise_exc, self.calls = parsed, stop_reason, raise_exc, []

    def parse(self, **kw):
        self.calls.append(kw)
        if self.raise_exc:
            raise self.raise_exc
        return SimpleNamespace(parsed_output=self.parsed, stop_reason=self.stop_reason,
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20))


def _fake_client(monkeypatch, messages):
    monkeypatch.setattr(S.anthropic, "Anthropic", lambda api_key, **kw: SimpleNamespace(messages=messages))


def test_claude_summary_uses_parse(tmp_path, monkeypatch):
    cfg = load_config(tmp_path, tmp_path)
    msgs = FakeMessages(S.SummaryModel(headline="H", body="B", risks=["r1"]))
    _fake_client(monkeypatch, msgs)
    s = S.claude_summary([f("orphans")], "", cfg, "sk-ant-x", LOG)
    assert s == Summary("H", "B", ["r1"])
    kw = msgs.calls[0]
    assert kw["model"] == cfg.model and kw["max_tokens"] == 1024
    assert kw["output_config"] == {"effort": "low"} and kw["output_format"] is S.SummaryModel
    assert kw["messages"][0]["role"] == "user"


def test_claude_summary_refusal_returns_none(tmp_path, monkeypatch):
    cfg = load_config(tmp_path, tmp_path)
    _fake_client(monkeypatch, FakeMessages(None, stop_reason="refusal"))
    assert S.claude_summary([f("orphans")], "", cfg, "k", LOG) is None


def test_claude_summary_exception_returns_none(tmp_path, monkeypatch):
    cfg = load_config(tmp_path, tmp_path)
    _fake_client(monkeypatch, FakeMessages(None, raise_exc=RuntimeError("boom")))
    assert S.claude_summary([f("orphans")], "", cfg, "k", LOG) is None


def test_summarize_falls_back_to_template_on_failure(tmp_path, monkeypatch):
    cfg = load_config(tmp_path, tmp_path)
    cfg.backend = "api"
    _fake_client(monkeypatch, FakeMessages(None, raise_exc=RuntimeError("boom")))
    s = S.summarize([f("orphans", title="Orphans")], "", cfg, "k", LOG)
    assert s.headline == "Orphans"
