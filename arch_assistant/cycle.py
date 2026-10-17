"""One assistant cycle: run checks, deduplicate, run `auto` actions, summarize, queue the rest. No Qt here."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable

from .actions import POST_HOOKS, effective_mode, needs_confirmation, run_action, running_vms
from .checks import run_all
from .config import Config, check_env
from .model import Action, ActionResult, Finding, Summary
from .state import State
from .summarizer import summarize


@dataclass
class Pending:
    finding: Finding
    action: Action
    reason: str | None = None

    @property
    def label(self) -> str:
        return f"{self.action.label}: {self.finding.title}"

    @property
    def key(self) -> tuple:
        return (self.finding.id, self.action.id, self.action.args)


@dataclass
class CycleResult:
    findings: list[Finding]
    new: list[Finding]
    summary: Summary | None
    auto_run: list[ActionResult] = field(default_factory=list)
    pending: list[Pending] = field(default_factory=list)       # every open action for the current findings
    new_pending: list[Pending] = field(default_factory=list)   # the subset that belongs to newly reported findings


def merge_findings(base: list[Finding], fresh: list[Finding]) -> list[Finding]:
    """Fresh post-action results replace earlier findings with the same id."""
    fresh_ids = {f.id for f in fresh}
    return [f for f in base if f.id not in fresh_ids] + list(fresh)


class Cycle:
    def __init__(self, cfg: Config, state: State, log: logging.Logger, api_key: str | None,
                 running_vms: Callable[[], list[str] | None] = running_vms,
                 now: Callable[[], datetime] = datetime.now):
        self.cfg, self.state, self.log, self.api_key = cfg, state, log, api_key
        self.running_vms, self.now = running_vms, now
        self._lock = threading.Lock()
        self.state.load()

    def run(self, only: Iterable[str] | None = None) -> CycleResult | None:
        """Full cycle, or None when another cycle (or action) is still running."""
        if not self._lock.acquire(blocking=False):
            self.log.warning("cycle requested while another cycle or action is running; skipped")
            return None
        try:
            return self._run(only)
        finally:
            self._lock.release()

    def _run(self, only: Iterable[str] | None) -> CycleResult:
        now = self.now()
        self.log.info("cycle start")
        findings = run_all(self.cfg.checks_dir, check_env(self.cfg), only)
        for f in findings:
            if f.error:
                self.log.warning("check %s failed: %s", f.id, f.error.splitlines()[0] if f.error else "")
        self.state.data["last_cycle_at"] = now.isoformat()
        new = self.state.new_findings(findings, self.cfg, now)
        new_ids = {f.id for f in new}
        result = CycleResult(findings, new, None)
        executed: set[tuple] = set()
        for f in new:
            for action in f.actions:
                mode = effective_mode(action.id, self.cfg, action.args)
                if mode != "auto":
                    continue
                reason = needs_confirmation(action, f, findings, self.running_vms)
                if reason is None:
                    res, extra = self._run_action(action)
                    result.auto_run.append(res)
                    result.findings = merge_findings(result.findings, extra)
                    executed.add((f.id, action.id, action.args))
        # the open queue is rebuilt from everything currently found, not only from what is new
        for f in result.findings:
            if f.is_empty or f.error or self.state.is_snoozed(f, now):
                continue
            for action in f.actions:
                if effective_mode(action.id, self.cfg, action.args) == "never" or (f.id, action.id, action.args) in executed:
                    continue
                p = Pending(f, action, needs_confirmation(action, f, result.findings, self.running_vms))
                result.pending.append(p)
                if f.id in new_ids:
                    result.new_pending.append(p)
        if new:
            news_text = "; ".join(f.summary for f in findings if f.id == "arch-news" and not f.is_empty)
            result.summary = summarize(new, news_text, self.cfg, self.api_key, self.log)
            for f in new:
                self.state.mark_reported(f, now)
        self.state.data["last_findings"] = [f.to_dict() for f in result.findings]
        self.state.save()
        self.log.info("cycle done: %d findings, %d new, %d auto, %d pending (%d new)",
                      len(result.findings), len(new), len(result.auto_run), len(result.pending), len(result.new_pending))
        return result

    def run_pending(self, pending: Pending) -> tuple[ActionResult, list[Finding]]:
        """Run a queued action on the user's request; holds the cycle lock while running."""
        with self._lock:
            res, extra = self._run_action(pending.action)
            self.state.save()
            return res, extra

    def _run_action(self, action: Action) -> tuple[ActionResult, list[Finding]]:
        res = run_action(self.cfg.actions_dir, action, self.log)
        if action.id == "update" and res.ok:
            self.state.mark_update_run(self.now())
        hooks = POST_HOOKS.get(action.id, ())
        extra = run_all(self.cfg.checks_dir, check_env(self.cfg), only=hooks) if hooks else []
        return res, extra

    def decline(self, pending: Pending) -> None:
        with self._lock:
            self.state.mark_declined(pending.finding, self.now(), self.cfg)
            self.state.save()
