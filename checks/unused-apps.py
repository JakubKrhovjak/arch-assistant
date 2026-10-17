#!/usr/bin/env python3
"""Check: explicitly installed packages with a desktop entry that have not been used for AA_UNUSED_DAYS days.

Last use = max(atime of the Exec binary, entries in ~/.local/share/recently-used.xbel). Env: AA_HOME.
"""
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path

KEEP = ("plasma-*", "kde-*", "systemd", "linux*", "xdg-*", "arch-assistant", "ghostty", "sddm*")
XBEL_NS = "{http://www.freedesktop.org/standards/desktop-bookmarks}"


def pacman(*args: str) -> list[str]:
    out = subprocess.run(["pacman", *args], capture_output=True, text=True)
    return [l for l in out.stdout.splitlines() if l.strip()]


def desktop_files(pkg: str) -> list[Path]:
    files = []
    for line in pacman("-Ql", pkg):
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].endswith(".desktop") and "/applications/" in parts[1]:
            files.append(Path(parts[1].strip()))
    return files


def parse_desktop(path: Path) -> tuple[str, str | None]:
    """(Name, path of the Exec binary or None when hidden / not resolvable)."""
    name, exec_line = path.stem, None
    try:
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith("Name=") and name == path.stem:
                name = line[5:].strip()
            elif line.startswith("Exec="):
                exec_line = line[5:].strip()
            elif line.startswith("NoDisplay=true"):
                return name, None
    except OSError:
        return name, None
    if not exec_line:
        return name, None
    tokens = exec_line.split()
    first = tokens[0]
    if first == "env":
        first = next((t for t in tokens[1:] if "=" not in t), first)
    binary = first if first.startswith("/") else shutil.which(first)
    return name, binary


def recently_used(home: Path) -> dict[str, datetime]:
    """app name / exec basename -> last modified, from recently-used.xbel."""
    path = home / ".local" / "share" / "recently-used.xbel"
    used: dict[str, datetime] = {}
    if not path.is_file():
        return used
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return used
    for app in root.iter(f"{XBEL_NS}application"):
        try:
            when = datetime.fromisoformat(app.get("modified", ""))   # "...Z" parses as UTC on Python 3.11+
        except ValueError:
            continue
        if when.tzinfo is None:
            when = datetime.combine(when.date(), when.time(), timezone.utc)
        keys = {app.get("name", "")}
        exec_attr = app.get("exec", "").strip("'\"")
        if exec_attr:
            keys.add(Path(exec_attr.split()[0]).name)
        for k in keys:
            if k and (k not in used or used[k] < when):
                used[k] = when
    return used


def main() -> None:
    days = int(os.environ.get("AA_UNUSED_DAYS", 90))
    home = Path(os.environ.get("AA_HOME") or Path.home())
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    used = recently_used(home)
    items = []
    for pkg in pacman("-Qqe"):
        if any(fnmatch(pkg, k) for k in KEEP):
            continue
        last: datetime | None = None
        app_name = None
        for df in desktop_files(pkg):
            name, binary = parse_desktop(df)
            if binary is None:
                continue
            app_name = app_name or name
            try:
                at = datetime.fromtimestamp(os.stat(binary).st_atime, timezone.utc)
            except OSError:
                continue
            for cand in (at, used.get(name), used.get(Path(binary).name)):
                if cand and (last is None or cand > last):
                    last = cand
        if app_name is None or last is None or last >= cutoff:
            continue
        items.append({"name": pkg, "app": app_name, "last_used": last.date().isoformat()})
    if not items:
        print(json.dumps({"id": "unused-apps", "severity": "none", "title": "No unused applications", "summary": ""}))
        return
    items.sort(key=lambda i: i["last_used"])
    print(json.dumps({
        "id": "unused-apps", "severity": "low",
        "title": f"{len(items)} application{'s' if len(items) != 1 else ''} unused for {days}+ days",
        "summary": ", ".join(f"{i['app']} ({i['name']}, last {i['last_used']})" for i in items[:8]),
        "items": items,
        "actions": [{"id": "remove-package", "label": f"Remove {i['app']} ({i['name']})", "args": [i["name"]]} for i in items],
    }))


if __name__ == "__main__":
    main()
