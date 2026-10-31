"""Shared helper: JetBrains IDE version directories under ~/.config, ~/.local/share and ~/.cache."""
from __future__ import annotations

import os
import re
from pathlib import Path

VERSION_RE = re.compile(r"^([A-Za-z]+)(\d{4}\.\d+)(?:-[A-Za-z0-9._-]+)?$")
DATA_DIR_RE = re.compile(r'"dataDirectoryName"\s*:\s*"([^"]+)"')
ROOTS = (".config/JetBrains", ".local/share/JetBrains", ".cache/JetBrains")
TOOLBOX_APPS = ".local/share/JetBrains/Toolbox/apps"


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
    """product -> version -> directories, e.g. {"GoLand": {"2025.2": [...], "2026.1": [...]}}.

    A backup such as IntelliJIdea2026.1-backup is filed under its own version "2026.1-backup".
    """
    found: dict[str, dict[str, list[Path]]] = {}
    for rel in ROOTS:
        root = home / rel
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            m = VERSION_RE.match(entry.name)
            if m and entry.is_dir():
                version = entry.name[len(m.group(1)):]
                found.setdefault(m.group(1), {}).setdefault(version, []).append(entry)
    return found


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("-", 1)[0].split("."))


def toolbox_data_dirs(home: Path) -> set[str]:
    """dataDirectoryName of every IDE installed by Toolbox; empty when there is no product-info.json."""
    apps = home / TOOLBOX_APPS
    names: set[str] = set()
    for pattern in ("*/product-info.json", "*/*/product-info.json", "*/*/*/product-info.json"):
        for info in apps.glob(pattern):
            try:
                names |= set(DATA_DIR_RE.findall(info.read_text(errors="replace")))
            except OSError:
                pass
    return names


def _highest_plain(versions: dict[str, list[Path]]) -> str:
    plain = [v for v in versions if "-" not in v]
    return max(plain, key=version_key) if plain else ""


def old_versions(home: Path) -> list[dict]:
    """Version directories of IDEs that are not current; sorted by product, version.

    Current = dataDirectoryName of the Toolbox-installed IDEs. Without any Toolbox product-info.json
    it falls back to the highest plain version per product. Backup directories are never current
    unless an installed IDE names them as its data directory.
    """
    installed = toolbox_data_dirs(home)
    out = []
    for product, versions in sorted(scan(home).items()):
        if installed:
            mine = sorted(n[len(product):] for n in installed if (m := VERSION_RE.match(n)) and m.group(1) == product)
            current = ", ".join(mine) or "not installed"
        else:
            current = _highest_plain(versions)
        for version in sorted(versions, key=lambda v: (version_key(v), v)):
            name = f"{product}{version}"
            if name in installed or (not installed and version == current):
                continue
            paths = versions[version]
            out.append({"name": name, "product": product, "version": version,
                        "current": current, "size_mb": sum(dir_size_mb(p) for p in paths),
                        "paths": [str(p) for p in paths]})
    return out


def current_versions(home: Path) -> set[str]:
    installed = toolbox_data_dirs(home)
    if installed:
        return installed
    return {f"{product}{h}" for product, versions in scan(home).items() if (h := _highest_plain(versions))}
