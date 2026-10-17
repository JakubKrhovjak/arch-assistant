"""Shared helper: JetBrains IDE version directories under ~/.config, ~/.local/share and ~/.cache."""
from __future__ import annotations

import os
import re
from pathlib import Path

VERSION_RE = re.compile(r"^([A-Za-z]+)(\d{4}\.\d+)$")
ROOTS = (".config/JetBrains", ".local/share/JetBrains", ".cache/JetBrains")


def home_dir() -> Path:
    return Path(os.environ.get("AA_HOME") or Path.home())


def dir_size_mb(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return round(total / 1_048_576)


def scan(home: Path) -> dict[str, dict[str, list[Path]]]:
    """product -> version -> directories, e.g. {"GoLand": {"2025.2": [...], "2026.1": [...]}}."""
    found: dict[str, dict[str, list[Path]]] = {}
    for rel in ROOTS:
        root = home / rel
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            m = VERSION_RE.match(entry.name)
            if m and entry.is_dir():
                found.setdefault(m.group(1), {}).setdefault(m.group(2), []).append(entry)
    return found


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def old_versions(home: Path) -> list[dict]:
    """Every version that has a newer one of the same product; sorted by product, version."""
    out = []
    for product, versions in sorted(scan(home).items()):
        current = max(versions, key=version_key)
        for version in sorted(versions, key=version_key):
            if version == current:
                continue
            paths = versions[version]
            out.append({"name": f"{product}{version}", "product": product, "version": version,
                        "current": current, "size_mb": sum(dir_size_mb(p) for p in paths),
                        "paths": [str(p) for p in paths]})
    return out


def current_versions(home: Path) -> set[str]:
    return {f"{product}{max(versions, key=version_key)}" for product, versions in scan(home).items()}
