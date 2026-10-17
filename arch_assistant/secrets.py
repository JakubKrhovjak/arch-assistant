"""Reads ANTHROPIC_API_KEY from a shell secrets file (e.g. ~/.config/zsh/secrets.zsh)."""
from __future__ import annotations

import re
from pathlib import Path

KEY_RE = re.compile(r"""^\s*(?:export\s+)?ANTHROPIC_API_KEY=["']?([^"'\s]+)""", re.MULTILINE)


def read_api_key(path: Path) -> str | None:
    """The key, or None when the file is missing, unreadable or has no such line. Never raises."""
    try:
        text = Path(path).expanduser().read_text()
    except OSError:
        return None
    m = KEY_RE.search(text)
    return m.group(1) if m else None
