#!/usr/bin/env python3
"""Action: remove GPU / Wi-Fi driver packages for absent hardware (pacman -Rns). Args: package names.

Re-reads the hardware and refuses any package the check would not flag right now. Never runs unattended.
"""
import subprocess
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "checks")]
import _drivers  # noqa: E402


def main(names: list[str]) -> int:
    if not names:
        print("Nothing to remove")
        return 0
    flagged = {i["name"] for i in _drivers.stale_packages(_drivers.read_lspci(), _drivers.read_installed())[0]}
    for name in names:   # validate everything before removing anything
        if name not in flagged:
            print(f"Refused: {name} is not a driver package for absent hardware")
            return 1
    proc = subprocess.run(["sudo", "/usr/bin/pacman", "-Rns", "--noconfirm", *names], capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        first = next((l for l in proc.stderr.splitlines() if "error" in l.lower()), (proc.stderr.splitlines() or [""])[0])
        print(f"Removing driver packages failed: {first}")
        return 1
    print(f"Removed {len(names)} driver package{'s' if len(names) != 1 else ''}: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
