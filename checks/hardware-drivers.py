#!/usr/bin/env python3
"""Check: GPU / Wi-Fi driver and firmware packages installed for hardware that is not present.

Hardware from `lspci -nn`, packages from `pacman -Qq` (both found on PATH, so tests can shim them).
"""
import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent)]
import _drivers  # noqa: E402


def hint(b: dict) -> str:
    if b["required_by"] == ["linux-firmware"]:
        return (f". {b['name']} is pulled in by the linux-firmware meta package - "
                "install the split linux-firmware-* packages explicitly to drop it")
    return f". {b['name']} is required by {', '.join(b['required_by'])} and was not offered"


def main() -> None:
    items, blocked = _drivers.stale_packages(_drivers.read_lspci(), _drivers.read_installed())
    if not items:
        print(json.dumps({"id": "hardware-drivers", "severity": "none", "title": "No driver packages for absent hardware", "summary": ""}))
        return
    names = [i["name"] for i in items]
    print(json.dumps({
        "id": "hardware-drivers", "severity": "low",
        "title": f"{len(items)} driver package{'s' if len(items) != 1 else ''} for hardware this machine does not have",
        "summary": ", ".join(f"{i['name']} ({i['hardware']})" for i in items) + "".join(hint(b) for b in blocked),
        "items": items,
        "actions": [{"id": "remove-driver-packages", "label": "Remove unneeded driver packages", "args": names}],
    }))


if __name__ == "__main__":
    main()
