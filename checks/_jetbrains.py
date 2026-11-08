"""Shared helper: superseded JetBrains IDE version directories.

Only the plugins (~/.local/share/JetBrains/<Name>) and caches (~/.cache/JetBrains/<Name>) of an old version are
ever offered for removal. Settings in ~/.config/JetBrains are never touched: an IDE update copies them only on
the new version's first start, and deleting them earlier loses them for good. A version counts as old only when
a newer version of the same product already has its own settings directory. Backups (IntelliJIdea2026.1-backup,
IntelliJIdea2026.3.pred-importem, anything after the version) are not versions and are never touched.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

VERSION_RE = re.compile(r"^([A-Za-z]+)(\d{4}\.\d+)$")
DATA_DIR_RE = re.compile(r'"dataDirectoryName"\s*:\s*"([^"]+)"')
CONFIG_ROOT = ".config/JetBrains"
DATA_ROOTS = (".local/share/JetBrains", ".cache/JetBrains")   # the only places anything is removed from
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


def _versions_in(root: Path) -> dict[str, dict[str, Path]]:
    """product -> version -> directory for plain version directories (no backups) directly in root."""
    found: dict[str, dict[str, Path]] = {}
    if root.is_dir():
        for entry in root.iterdir():
            m = VERSION_RE.match(entry.name)
            if m and entry.is_dir() and not entry.is_symlink():
                found.setdefault(m.group(1), {})[m.group(2)] = entry
    return found


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


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


def old_versions(home: Path) -> list[dict]:
    """Plugin and cache directories of superseded IDE versions; sorted by product, version.

    Old = not an installed IDE's dataDirectoryName (Toolbox product-info.json) and a newer version of the same
    product has a settings directory in ~/.config/JetBrains. `paths` never contains anything under ~/.config.
    """
    installed = toolbox_data_dirs(home)
    configs = _versions_in(home / CONFIG_ROOT)
    data: dict[str, dict[str, list[Path]]] = {}
    for rel in DATA_ROOTS:
        for product, versions in _versions_in(home / rel).items():
            for version, path in versions.items():
                data.setdefault(product, {}).setdefault(version, []).append(path)
    out = []
    for product, versions in sorted(data.items()):
        configured = configs.get(product, {})
        for version in sorted(versions, key=version_key):
            name = f"{product}{version}"
            newer = [v for v in configured if version_key(v) > version_key(version)]
            if name in installed or not newer:
                continue
            current = max(newer, key=version_key)
            paths = versions[version]
            out.append({"name": name, "product": product, "version": version, "current": current,
                        "size_mb": sum(dir_size_mb(p) for p in paths), "paths": [str(p) for p in paths]})
    return out


def current_versions(home: Path) -> set[str]:
    """Installed IDEs (Toolbox) plus the newest settings directory of every product: never removed."""
    newest = {f"{product}{max(versions, key=version_key)}" for product, versions in _versions_in(home / CONFIG_ROOT).items()}
    return toolbox_data_dirs(home) | newest
