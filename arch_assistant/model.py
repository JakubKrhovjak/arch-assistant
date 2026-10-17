"""Data types: check finding, action, action result, summary."""
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
        """Parse a finding from the check JSON contract; missing fields or unknown severity raise ValueError."""
        try:
            d = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e}") from e
        if not isinstance(d, dict):
            raise ValueError("JSON is not an object")
        missing = [k for k in ("id", "severity", "title") if k not in d]
        if missing:
            raise ValueError(f"missing fields {', '.join(missing)}")
        if d["severity"] not in SEVERITIES:
            raise ValueError(f"unknown severity {d['severity']!r}")
        items = d.get("items") or []
        actions = d.get("actions") or []
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise ValueError("items must be a list of objects")
        if not isinstance(actions, list) or not all(isinstance(a, dict) and "id" in a for a in actions):
            raise ValueError("actions must be a list of objects with an id")
        return cls(
            id=str(d["id"]),
            severity=d["severity"],
            title=str(d["title"]),
            summary=str(d.get("summary", "")),
            items=tuple(dict(i) for i in items),
            actions=tuple(Action.from_dict(a) for a in actions),
        )

    @classmethod
    def failed(cls, check_id: str, error: str) -> Finding:
        first = error.strip().splitlines()[0] if error.strip() else "no output"
        return cls(id=check_id, severity="low", title=f"Check {check_id} failed",
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
