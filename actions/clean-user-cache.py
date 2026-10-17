#!/usr/bin/env python3
"""Action: delete ~/.cache/<name> for names on the safe list only. Args: cache directory names. Env: AA_HOME."""
import shutil
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "checks")]
import _caches  # noqa: E402
import _jetbrains  # noqa: E402


def main(names: list[str]) -> int:
    if not names:
        print("Nothing to remove")
        return 0
    for name in names:   # validate everything before deleting anything
        if name not in _caches.SAFE_CACHES or "/" in name:
            print(f"Refused: {name} is not in the list of safe caches")
            return 1
    cache = _jetbrains.home_dir() / ".cache"
    freed, removed = 0, 0
    for name in names:
        path = cache / name
        if not path.is_dir():
            continue
        freed += _jetbrains.dir_size_mb(path)
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
        print(f"deleted {path}", file=sys.stderr)
    print(f"Removed {removed} cache{'s' if removed != 1 else ''}, freed {freed} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
