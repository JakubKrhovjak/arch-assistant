#!/usr/bin/env python3
"""Action: delete the plugins and caches of superseded JetBrains IDE versions. Args: names like GoLand2025.2.

Re-scans the directories itself, refuses any name that is a current version, and deletes only directories
directly under ~/.local/share/JetBrains or ~/.cache/JetBrains; settings in ~/.config/JetBrains are never deleted.
"""
import shutil
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "checks")]
import _jetbrains  # noqa: E402


def main(names: list[str]) -> int:
    if not names:
        print("Nothing to remove")
        return 0
    home = _jetbrains.home_dir()
    old = {o["name"]: o for o in _jetbrains.old_versions(home)}
    current = _jetbrains.current_versions(home)
    allowed = {(home / rel).resolve() for rel in _jetbrains.DATA_ROOTS}
    for name in names:   # validate everything before deleting anything
        if name in current:
            print(f"Refused: {name} is the current version")
            return 1
        if name not in old:
            print(f"Refused: {name} is not a known old version")
            return 1
        for p in old[name]["paths"]:
            if Path(p).resolve().parent not in allowed:
                print(f"Refused: {p} is outside the JetBrains plugin and cache directories")
                return 1
    removed = []
    for name in names:
        for p in old[name]["paths"]:
            shutil.rmtree(p, ignore_errors=False)
            print(f"deleted {p}", file=sys.stderr)
        removed.append(f"{name} ({old[name]['size_mb']} MB)")
    print("Removed: " + ", ".join(removed))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
