#!/usr/bin/env python3
"""Action: delete ~/.cache/JetBrains/Toolbox/temp and /download (Toolbox re-creates them). Takes no args. Env: AA_HOME."""
import shutil
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "checks")]
import _jetbrains  # noqa: E402

LEFTOVERS = ("temp", "download")


def main(_args: list[str]) -> int:
    base = _jetbrains.home_dir() / ".cache" / "JetBrains" / "Toolbox"
    freed = 0
    for name in LEFTOVERS:
        path = base / name
        if not path.is_dir() or path.is_symlink():
            continue
        freed += _jetbrains.dir_size_mb(path)
        shutil.rmtree(path, ignore_errors=True)
        print(f"deleted {path}", file=sys.stderr)
    print(f"Removed Toolbox temp and download, freed {freed} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
