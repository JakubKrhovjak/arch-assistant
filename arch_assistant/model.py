"""Datové typy: nález kontroly, akce, výsledek akce, shrnutí."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["none", "low", "medium", "high", "critical"]
SEVERITIES: tuple[str, ...] = ("none", "low", "medium", "high", "critical")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    args: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict) -> Action:
        return cls(str(d["id"]), str(d.get("label", d["id"])), tuple(str(a) for a in d.get("args", [])))


@dataclass(frozen=True)
class Finding:
    id: str
    severity: Severity
    title: str
    summary: str
    items: tuple[dict, ...] = ()
    actions: tuple[Action, ...] = ()
    error: str | None = None

    @classmethod
    def from_json(cls, text: str) -> Finding:
        """Načte nález z JSON kontraktu kontroly; chybějící pole nebo neznámá severity = ValueError."""
        try:
            d = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"nevalidní JSON: {e}") from e
        if not isinstance(d, dict):
            raise ValueError("JSON není objekt")
        missing = [k for k in ("id", "severity", "title") if k not in d]
        if missing:
            raise ValueError(f"chybí pole {', '.join(missing)}")
        if d["severity"] not in SEVERITIES:
            raise ValueError(f"neznámá severity {d['severity']!r}")
        return cls(
            id=str(d["id"]),
            severity=d["severity"],
            title=str(d["title"]),
            summary=str(d.get("summary", "")),
            items=tuple(dict(i) for i in d.get("items", [])),
            actions=tuple(Action.from_dict(a) for a in d.get("actions", [])),
        )

    @classmethod
    def failed(cls, check_id: str, error: str) -> Finding:
        first = error.strip().splitlines()[0] if error.strip() else "bez výstupu"
        return cls(id=check_id, severity="low", title=f"Kontrola {check_id} selhala",
                   summary=first, error=error)

    @property
    def is_empty(self) -> bool:
        return self.severity == "none"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "severity": self.severity, "title": self.title, "summary": self.summary,
            "items": list(self.items),
            "actions": [{"id": a.id, "label": a.label, "args": list(a.args)} for a in self.actions],
            "error": self.error,
        }


@dataclass
class ActionResult:
    action_id: str
    ok: bool
    output: str
    detail: str
    duration_s: float


@dataclass
class Summary:
    headline: str
    body: str
    risks: list[str] = field(default_factory=list)
