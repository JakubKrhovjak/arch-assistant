#!/usr/bin/env python3
"""Check: plugins and caches of superseded JetBrains IDE versions (settings are never offered). Env: AA_HOME."""
import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent)]
import _jetbrains  # noqa: E402


def main() -> None:
    old = _jetbrains.old_versions(_jetbrains.home_dir())
    if not old:
        print(json.dumps({"id": "jetbrains-old", "severity": "none", "title": "No old JetBrains IDE plugins or caches", "summary": ""}))
        return
    total = sum(o["size_mb"] for o in old)
    items = [{"name": o["name"], "size_mb": o["size_mb"], "paths": o["paths"]} for o in old]
    summary = ", ".join(f"{o['product']} {o['version']} (current {o['current']})" for o in old)
    print(json.dumps({
        "id": "jetbrains-old", "severity": "low",
        "title": f"Old JetBrains IDE plugins and caches: {total} MB",
        "summary": summary, "items": items,
        "actions": [{"id": "clean-jetbrains-old", "label": "Remove old IDE plugins and caches", "args": [o["name"] for o in old]}],
    }))


if __name__ == "__main__":
    main()
