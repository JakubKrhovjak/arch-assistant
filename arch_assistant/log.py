"""Logování do ~/.local/state/arch-assistant/log/assistant.log s rotací."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_logger(state_dir: Path, name: str = "arch_assistant") -> logging.Logger:
    log = logging.getLogger(name)
    if getattr(log, "_aa_configured", False):
        return log
    log_dir = state_dir / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "assistant.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log._aa_configured = True  # type: ignore[attr-defined]
    return log
