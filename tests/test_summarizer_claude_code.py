import json
import logging
import subprocess
from types import SimpleNamespace

from arch_assistant import summarizer as S
from arch_assistant.config import load_config
from arch_assistant.model import Action, Finding, Summary

LOG = logging.getLogger("test")


def f(id="orphans"):
    return Finding(id, "low", "T", "S", (), (Action("x", "X"),))


def _result(structured, is_error=False, result="", rc=0):
    payload = {"type": "result", "is_error": is_error, "result": result, "structured_output": structured,
               "total_cost_usd": 0.001, "usage": {"input_tokens": 500, "output_tokens": 100}}
    return SimpleNamespace(returncode=rc, stdout=json.dumps(payload), stderr="")


def test_backend_default_is_claude_code(tmp_path):
    assert load_config(tmp_path, tmp_path).backend == "claude-code"


def test_backend_from_config(tmp_path):
    (tmp_path / "config.toml").write_text('backend = "api"\n')
    assert load_config(tmp_path, tmp_path).backend == "api"


def test_claude_code_summary_runs_headless_claude(tmp_path, monkeypatch):
    cfg = load_config(tmp_path, tmp_path)
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return _result({"headline": "H", "body": "B", "risks": ["r"]})

    monkeypatch.setattr(S.subprocess, "run", fake_run)
    monkeypatch.setattr(S.shutil, "which", lambda name: "/usr/bin/claude")
    s = S.claude_code_summary([f()], "", cfg, LOG)
    assert s == Summary("H", "B", ["r"])
    cmd, kw = calls[0]
    assert cmd[0] == "/usr/bin/claude" and "-p" in cmd and "--output-format" in cmd and "json" in cmd
    assert cmd[cmd.index("--model") + 1] == cfg.model
    assert "--json-schema" in cmd and "--no-session-persistence" in cmd and "--setting-sources" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "ANTHROPIC_API_KEY" not in kw["env"]
    assert kw["cwd"] == str(cfg.state_dir)
    assert kw["timeout"] == 90


def test_claude_code_summary_error_result_returns_none(tmp_path, monkeypatch):
    cfg = load_config(tmp_path, tmp_path)
    monkeypatch.setattr(S.subprocess, "run", lambda *a, **k: _result(None, is_error=True, result="Not logged in"))
    monkeypatch.setattr(S.shutil, "which", lambda name: "/usr/bin/claude")
    assert S.claude_code_summary([f()], "", cfg, LOG) is None


def test_claude_code_summary_timeout_returns_none(tmp_path, monkeypatch):
    cfg = load_config(tmp_path, tmp_path)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=90)

    monkeypatch.setattr(S.subprocess, "run", boom)
    monkeypatch.setattr(S.shutil, "which", lambda name: "/usr/bin/claude")
    assert S.claude_code_summary([f()], "", cfg, LOG) is None


def test_claude_code_summary_without_binary_returns_none(tmp_path, monkeypatch):
    cfg = load_config(tmp_path, tmp_path)
    monkeypatch.setattr(S.shutil, "which", lambda name: None)
    assert S.claude_code_summary([f()], "", cfg, LOG) is None


def test_summarize_prefers_backend_order(tmp_path, monkeypatch):
    cfg = load_config(tmp_path, tmp_path)
    cfg.backend = "claude-code"
    monkeypatch.setattr(S, "claude_code_summary", lambda *a, **k: Summary("CC", "B", []))
    monkeypatch.setattr(S, "claude_summary", lambda *a, **k: Summary("API", "B", []))
    assert S.summarize([f()], "", cfg, "key", LOG).headline == "CC"
    cfg.backend = "api"
    assert S.summarize([f()], "", cfg, "key", LOG).headline == "API"
    cfg.backend = "template"
    assert S.summarize([f()], "", cfg, "key", LOG).headline == "T"


def test_summarize_claude_code_falls_back_to_api_then_template(tmp_path, monkeypatch):
    cfg = load_config(tmp_path, tmp_path)
    monkeypatch.setattr(S, "claude_code_summary", lambda *a, **k: None)
    monkeypatch.setattr(S, "claude_summary", lambda *a, **k: Summary("API", "B", []))
    assert S.summarize([f()], "", cfg, "key", LOG).headline == "API"
    assert S.summarize([f()], "", cfg, None, LOG).headline == "T"
