"""What the package modules share: the sources, their labels, the argument format of the package actions and the
runner for read-only commands (pacman -Q*, flatpak list/search). No Qt here."""
from __future__ import annotations

import os
import subprocess

# Package sources in display order, with the label the window shows for each
SOURCE_LABELS = {"repo": "Arch", "aur": "AUR", "flatpak": "Flatpak"}
SOURCES = tuple(SOURCE_LABELS)
COMMAND_TIMEOUT = 60


def package_arg(source: str, package_id: str) -> str:
    """Argument of actions/update.sh, install.sh and uninstall.sh: "<source>:<package or Flatpak id>".
    The scripts parse it in parse_args (actions/_lib.sh)."""
    return f"{source}:{package_id}"


def split_arg(arg: str) -> tuple[str, str]:
    """The reverse of package_arg: (source, id)."""
    source, _, package_id = arg.partition(":")
    return source, package_id


def run_command(cmd: list[str]) -> subprocess.CompletedProcess | None:
    """A read-only command with the C locale (pacman's messages are parsed); None when it is missing or hangs."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False,
                              env={**os.environ, "LC_ALL": "C.UTF-8"})
    except (OSError, subprocess.TimeoutExpired):
        return None


def run_readonly(cmd: list[str], codes: tuple[int, ...] = (0,)) -> str | None:
    """stdout of a read-only command; None when it is missing, hangs or exits with a code not in `codes`."""
    proc = run_command(cmd)
    return proc.stdout if proc is not None and proc.returncode in codes else None
