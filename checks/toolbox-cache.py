#!/usr/bin/env python3
"""Check: JetBrains Toolbox download/temp leftovers in ~/.cache/JetBrains/Toolbox above AA_TOOLBOX_CACHE_MB. Env: AA_HOME."""
import json
import os
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent)]
import _jetbrains  # noqa: E402

LEFTOVERS = ("temp", "download")


def main() -> None:
    limit = float(os.environ.get("AA_TOOLBOX_CACHE_MB", 100))
    base = _jetbrains.home_dir() / ".cache" / "JetBrains" / "Toolbox"
    items = [{"name": n, "size_mb": _jetbrains.dir_size_mb(base / n), "paths": [str(base / n)]}
             for n in LEFTOVERS if (base / n).is_dir() and not (base / n).is_symlink()]
    total = sum(i["size_mb"] for i in items)
    if not items or total <= limit:
        print(json.dumps({"id": "toolbox-cache", "severity": "none", "title": "No JetBrains Toolbox leftovers", "summary": ""}))
        return
    print(json.dumps({
        "id": "toolbox-cache", "severity": "low",
        "title": f"JetBrains Toolbox leftovers: {total} MB",
        "summary": ", ".join(f"{i['name']} {i['size_mb']} MB" for i in items),
        "items": items,
        "actions": [{"id": "clean-toolbox-cache", "label": "Delete Toolbox temp and download", "args": []}],
    }))


if __name__ == "__main__":
    main()
