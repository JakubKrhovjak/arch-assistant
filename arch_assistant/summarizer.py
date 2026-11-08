"""The notification summary: a plain template over the new findings."""
from __future__ import annotations

from .model import SEVERITY_RANK, Finding, Summary

SEVERITY_LABEL = {"critical": "Critical", "high": "Important", "medium": "Medium", "low": "Minor"}
MAX_BODY_LINES = 5


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
