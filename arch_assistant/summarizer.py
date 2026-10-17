"""Turns findings into a notification summary: Claude (one structured call) or a template fallback."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

import anthropic
from pydantic import BaseModel

from .config import Config
from .model import SEVERITY_RANK, Finding, Summary

SEVERITY_LABEL = {"critical": "Critical", "high": "Important", "medium": "Medium", "low": "Minor"}
MAX_BODY_LINES = 5
MAX_ITEMS_IN_PROMPT = 10
MAX_TOKENS = 1024

PROMPT_INSTRUCTIONS = (
    "You write the system notification for an Arch Linux desktop maintenance assistant. "
    "Below is a JSON list of findings from deterministic checks, plus recent Arch news text. "
    "Write `headline` (max 60 characters) and `body` (max 400 characters, plain text, may use line breaks) "
    "for the notification: factual, no greeting, no preamble, say what was found and what the user can do. "
    "Put into `risks` at most 3 concrete risks (each under 120 characters) that follow from the Arch news "
    "or from critical packages (kernel, systemd, Plasma, virtualization); otherwise leave it empty. "
    "Do not invent facts that are not in the findings.\n"
)
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"headline": {"type": "string"}, "body": {"type": "string"},
                   "risks": {"type": "array", "items": {"type": "string"}}},
    "required": ["headline", "body", "risks"], "additionalProperties": False,
}
CLAUDE_CODE_TIMEOUT = 90


class SummaryModel(BaseModel):
    headline: str
    body: str
    risks: list[str]


def template_summary(findings: list[Finding]) -> Summary:
    if len(findings) == 1:
        f = findings[0]
        return Summary(f.title, f"• {f.title} – {f.summary}" if f.summary else f"• {f.title}", [])
    top = max(findings, key=lambda f: SEVERITY_RANK[f.severity])
    headline = f"{len(findings)} things to deal with: {SEVERITY_LABEL.get(top.severity, top.severity)}"
    lines = [f"• {f.title} – {f.summary}" if f.summary else f"• {f.title}" for f in findings[:MAX_BODY_LINES]]
    if len(findings) > MAX_BODY_LINES:
        lines.append(f"… and {len(findings) - MAX_BODY_LINES} more")
    return Summary(headline, "\n".join(lines), [])


PRIVATE_ITEM_KEYS = {"paths", "link"}


def _public(value):
    """Drop item fields that carry filesystem paths under /home (spec: nothing from /home reaches the model)."""
    if isinstance(value, str):
        return None if "/home/" in value else value
    if isinstance(value, list):
        cleaned = [_public(v) for v in value]
        return [v for v in cleaned if v is not None]
    return value


def _compact_item(item: dict) -> dict:
    out = {}
    for k, v in item.items():
        if k in PRIVATE_ITEM_KEYS:
            continue
        pv = _public(v)
        if pv is None and k == "name":
            pv = str(v).rsplit("/", 1)[-1]
        if pv is not None:
            out[k] = pv
    return out


def _compact(f: Finding) -> dict:
    d = {"id": f.id, "severity": f.severity, "title": f.title, "summary": f.summary,
         "actions": [a.label for a in f.actions]}
    d["items"] = {"count": len(f.items)} if len(f.items) > MAX_ITEMS_IN_PROMPT else [_compact_item(i) for i in f.items]
    return d


def build_prompt(findings: list[Finding], news_text: str) -> str:
    payload = json.dumps([_compact(f) for f in findings], ensure_ascii=False)
    news = f"\nArch news (last 30 days, manual intervention):\n{news_text.strip()}\n" if news_text.strip() else ""
    return f"{PROMPT_INSTRUCTIONS}\nFindings:\n{payload}\n{news}"


def claude_summary(findings: list[Finding], news_text: str, cfg: Config, api_key: str,
                   log: logging.Logger) -> Summary | None:
    """One structured call; any failure or refusal returns None so the caller falls back to the template."""
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.parse(
            model=cfg.model,
            max_tokens=MAX_TOKENS,
            output_config={"effort": "low"},
            output_format=SummaryModel,
            messages=[{"role": "user", "content": build_prompt(findings, news_text)}],
        )
    except Exception as e:  # SDK errors, network, schema mismatch
        log.warning("claude summary failed: %s: %s", type(e).__name__, e)
        return None
    usage = getattr(response, "usage", None)
    if usage is not None:
        log.info("claude summary tokens: input=%s output=%s model=%s",
                 getattr(usage, "input_tokens", "?"), getattr(usage, "output_tokens", "?"), cfg.model)
    if getattr(response, "stop_reason", None) == "refusal" or response.parsed_output is None:
        log.warning("claude summary unusable: stop_reason=%s", getattr(response, "stop_reason", None))
        return None
    out = response.parsed_output
    return Summary(out.headline.strip(), out.body.strip(), [r.strip() for r in out.risks if r.strip()])


def claude_code_summary(findings: list[Finding], news_text: str, cfg: Config, log: logging.Logger) -> Summary | None:
    """Headless `claude -p` under the user's Claude subscription (no API key). None on any failure."""
    claude = shutil.which("claude")
    if not claude:
        log.warning("claude-code backend: `claude` binary not found")
        return None
    cmd = [claude, "-p", "--output-format", "json", "--model", cfg.model, "--tools", "",
           "--no-session-persistence", "--setting-sources", "", "--json-schema", json.dumps(SUMMARY_SCHEMA),
           build_prompt(findings, news_text)]
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}   # force the subscription login
    try:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLAUDE_CODE_TIMEOUT,
                              env=env, cwd=str(cfg.state_dir), check=False)
        data = json.loads(proc.stdout)
    except subprocess.TimeoutExpired:
        log.warning("claude-code backend: timeout after %s s", CLAUDE_CODE_TIMEOUT)
        return None
    except (OSError, json.JSONDecodeError) as e:
        log.warning("claude-code backend failed: %s: %s", type(e).__name__, e)
        return None
    if proc.returncode != 0 or data.get("is_error") or not isinstance(data.get("structured_output"), dict):
        log.warning("claude-code backend error: %s", str(data.get("result", proc.stderr))[:200])
        return None
    usage = data.get("usage") or {}
    log.info("claude-code summary: model=%s cost=%s input=%s output=%s", cfg.model, data.get("total_cost_usd"),
             usage.get("input_tokens"), usage.get("output_tokens"))
    try:
        out = SummaryModel.model_validate(data["structured_output"])
    except Exception as e:
        log.warning("claude-code backend: invalid structured output: %s", e)
        return None
    return Summary(out.headline.strip(), out.body.strip(), [r.strip() for r in out.risks if r.strip()][:3])


def summarize(findings: list[Finding], news_text: str, cfg: Config, api_key: str | None,
              log: logging.Logger) -> Summary:
    """backend order: claude-code (subscription) -> api (key) -> template; each falls through on failure."""
    if cfg.backend == "claude-code":
        s = claude_code_summary(findings, news_text, cfg, log)
        if s is not None:
            return s
    if cfg.backend in ("claude-code", "api") and api_key:
        s = claude_summary(findings, news_text, cfg, api_key, log)
        if s is not None:
            return s
    return template_summary(findings)
