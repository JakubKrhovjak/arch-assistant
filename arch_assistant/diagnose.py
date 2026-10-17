"""On-demand diagnosis of one finding: a collector script gathers context, Claude explains and suggests commands.

Nothing here runs a fix. The suggested commands are shown to the user to copy; the no-click-no-action rule stays.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from .config import REPO_ROOT, Config, check_env
from .model import Finding
from .summarizer import structured_call

DIAGNOSE_DIR = REPO_ROOT / "diagnose"
MAX_CONTEXT_CHARS = 20000
COLLECT_TIMEOUT = 60
DIAGNOSE_MAX_TOKENS = 2048

PROMPT = (
    "You are diagnosing one finding of an Arch Linux desktop maintenance assistant (KDE Plasma, systemd, pacman/yay, "
    "libvirt, Docker). Explain in `explanation` (plain text, max 900 characters, concrete, no preamble) what the "
    "finding means and the most likely root cause, based only on the context below. Put into `commands` the shell "
    "commands that would fix or further inspect it, one per entry, safest first; prefer read-only commands when the "
    "cause is uncertain; never include commands that delete user data. Set `confidence` to high, medium or low.\n"
)


class DiagnosisModel(BaseModel):
    explanation: str
    commands: list[str]
    confidence: str


@dataclass
class Diagnosis:
    explanation: str
    commands: list[str] = field(default_factory=list)
    confidence: str = "medium"


def collect_context(finding: Finding, cfg: Config, log: logging.Logger, diagnose_dir: Path = DIAGNOSE_DIR) -> str:
    """Run diagnose/<finding id>.sh with the finding JSON on stdin; fall back to the finding JSON itself."""
    payload = json.dumps(finding.to_dict(), ensure_ascii=False)   # one line: collectors read it with `read -r`
    script = next((p for p in (diagnose_dir / f"{finding.id}.sh", diagnose_dir / f"{finding.id}.py") if p.is_file()), None)
    if script is None:
        return payload
    try:
        proc = subprocess.run([str(script)], input=payload, capture_output=True, text=True, timeout=COLLECT_TIMEOUT,
                              env={**os.environ, **check_env(cfg)}, check=False)
        out = proc.stdout
        if proc.returncode != 0:
            log.warning("diagnose collector %s exited %s: %s", script.name, proc.returncode, proc.stderr.strip()[:200])
            out = out or payload
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("diagnose collector %s failed: %s", script.name, e)
        return payload
    if len(out) > MAX_CONTEXT_CHARS:
        out = out[:MAX_CONTEXT_CHARS] + "\n[truncated]"
    return out


def build_prompt(finding: Finding, context: str) -> str:
    head = f"Finding: {finding.title}\nSeverity: {finding.severity}\nSummary: {finding.summary}\n"
    return f"{PROMPT}\n{head}\nContext:\n{context}\n"


def diagnose(finding: Finding, cfg: Config, api_key: str | None, log: logging.Logger, context: str) -> Diagnosis | None:
    out = structured_call(build_prompt(finding, context), DiagnosisModel, cfg, api_key, log, max_tokens=DIAGNOSE_MAX_TOKENS)
    if out is None:
        return None
    return Diagnosis(out.explanation.strip(), [c.strip() for c in out.commands if c.strip()], out.confidence.strip() or "medium")
