"""State between runs: what was reported and declined, check baselines, finding deduplication."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path

from .config import Config
from .model import Finding

EMPTY: dict = {
    "reported": {}, "declined": {}, "baseline": {},
    "last_update_report": None, "last_update_run": None, "last_findings": [],
}


def fingerprint(f: Finding) -> str:
    """Finding fingerprint: id, title, summary and item names (sizes are ignored)."""
    names = sorted(
        str(i["name"]) if "name" in i else json.dumps({k: v for k, v in i.items() if k != "size_mb"}, sort_keys=True)
        for i in f.items
    )
    raw = "\x1f".join([f.id, f.title, f.summary, *names])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _pkgs(f: Finding) -> dict[str, str]:
    """name -> pending version for an updates finding."""
    return {str(i.get("name", "")): str(i.get("new", "")) for i in f.items}


def _critical(pkgs: dict[str, str], patterns: list[str]) -> set[tuple[str, str]]:
    return {(n, v) for n, v in pkgs.items() if any(fnmatch(n, p) for p in patterns)}


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = json.loads(json.dumps(EMPTY))
        self._lock = threading.RLock()

    def load(self) -> State:
        with self._lock:
            if self.path.is_file():
                try:
                    self.data = {**json.loads(json.dumps(EMPTY)), **json.loads(self.path.read_text())}
                except (json.JSONDecodeError, OSError):
                    pass
        return self

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))
            tmp.rename(self.path)

    # --- deduplication -----------------------------------------------------

    def is_declined(self, f: Finding) -> bool:
        return self.data["declined"].get(f.id) == fingerprint(f)

    def should_report(self, f: Finding, cfg: Config, now: datetime) -> bool:
        if f.is_empty:
            return False
        if f.id == "updates":
            return self._should_report_updates(f, cfg, now)
        fp = fingerprint(f)
        if self.data["declined"].get(f.id) == fp:
            return False
        rep = self.data["reported"].get(f.id)
        if rep is None or rep["fp"] != fp:
            return True
        return now - datetime.fromisoformat(rep["at"]) >= timedelta(days=cfg.thresholds["report_days"])

    def _should_report_updates(self, f: Finding, cfg: Config, now: datetime) -> bool:
        """A critical (name, version) new since the last report, or >= update_min_count packages in a
        different batch than last reported, or report_days elapsed. A completed update resets the record."""
        last = self.data.get("last_update_report") or {"at": None, "pkgs": {}}
        pkgs, last_pkgs = _pkgs(f), dict(last.get("pkgs", {}))
        if _critical(pkgs, cfg.critical_packages) - _critical(last_pkgs, cfg.critical_packages):
            return True
        if len(pkgs) >= cfg.thresholds["update_min_count"] and set(pkgs.items()) != set(last_pkgs.items()):
            return True
        if last["at"] is None:
            return False
        return now - datetime.fromisoformat(last["at"]) >= timedelta(days=cfg.thresholds["report_days"])

    def new_findings(self, findings: list[Finding], cfg: Config, now: datetime) -> list[Finding]:
        return [f for f in findings if self.should_report(f, cfg, now)]

    # --- writes -------------------------------------------------------------

    def mark_reported(self, f: Finding, now: datetime) -> None:
        with self._lock:
            self.data["reported"][f.id] = {"fp": fingerprint(f), "at": now.isoformat()}
            if f.id == "updates":
                self.data["last_update_report"] = {"at": now.isoformat(), "pkgs": _pkgs(f)}

    def mark_declined(self, f: Finding) -> None:
        with self._lock:
            self.data["declined"][f.id] = fingerprint(f)

    def mark_update_run(self, now: datetime) -> None:
        with self._lock:
            self.data["last_update_run"] = now.isoformat()
            self.data["last_update_report"] = None
