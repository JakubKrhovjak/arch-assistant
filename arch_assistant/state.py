"""Stav mezi běhy: co bylo hlášeno a odmítnuto, baseline kontrol, deduplikace nálezů."""
from __future__ import annotations

import hashlib
import json
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
    """Otisk nálezu: id, titulek, shrnutí a jména položek (velikosti se neberou v úvahu)."""
    names = sorted(
        str(i["name"]) if "name" in i else json.dumps({k: v for k, v in i.items() if k != "size_mb"}, sort_keys=True)
        for i in f.items
    )
    raw = "\x1f".join([f.id, f.title, f.summary, *names])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _names(f: Finding) -> list[str]:
    return [str(i.get("name", "")) for i in f.items]


def _critical(names: list[str], patterns: list[str]) -> set[str]:
    return {n for n in names if any(fnmatch(n, p) for p in patterns)}


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = json.loads(json.dumps(EMPTY))

    def load(self) -> State:
        if self.path.is_file():
            try:
                self.data = {**json.loads(json.dumps(EMPTY)), **json.loads(self.path.read_text())}
            except (json.JSONDecodeError, OSError):
                pass
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))
        tmp.rename(self.path)

    # --- deduplikace -------------------------------------------------------

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
        """Zásadní balíček nový oproti poslednímu hlášení, nebo >= update_min_count, nebo report_days."""
        last = self.data.get("last_update_report") or {"at": None, "names": []}
        names = _names(f)
        new_critical = _critical(names, cfg.critical_packages) - _critical(last["names"], cfg.critical_packages)
        if new_critical:
            return True
        if len(names) >= cfg.thresholds["update_min_count"] and len(names) > len(last["names"]):
            return True
        if last["at"] is None:
            return False
        return now - datetime.fromisoformat(last["at"]) >= timedelta(days=cfg.thresholds["report_days"])

    def new_findings(self, findings: list[Finding], cfg: Config, now: datetime) -> list[Finding]:
        return [f for f in findings if self.should_report(f, cfg, now)]

    # --- zápisy -------------------------------------------------------------

    def mark_reported(self, f: Finding, now: datetime) -> None:
        self.data["reported"][f.id] = {"fp": fingerprint(f), "at": now.isoformat()}
        if f.id == "updates":
            self.data["last_update_report"] = {"at": now.isoformat(), "names": _names(f)}

    def mark_declined(self, f: Finding) -> None:
        self.data["declined"][f.id] = fingerprint(f)

    def mark_update_run(self, now: datetime) -> None:
        self.data["last_update_run"] = now.isoformat()
