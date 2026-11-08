"""What must never be uninstalled, and the guard that checks a removal before it is confirmed. No Qt here.

`pacman -Rns` removes the picked packages together with every dependency nothing else needs, so checking only the
picked names is not enough: the guard asks pacman for the real removal set (`pacman -Rs --print`, read-only, no
root) and refuses it when it reaches a protected package (assets/protected.txt), the login shell of the user or
root, or a package Arch Assistant itself needs (assets/dependencies.txt).

actions/_lib.sh (guard_removal) runs the same check again right before `sudo /usr/bin/pacman -Rns`; both read the same two
files, so a change to what is protected is made there, never in code.
"""
from __future__ import annotations

import os
import pwd
from collections.abc import Iterable
from dataclasses import dataclass, replace
from fnmatch import fnmatch
from pathlib import Path

from .config import REPO_ROOT
from .packages import run_command, run_readonly, split_arg

PROTECTED_FILE = REPO_ROOT / "assets" / "protected.txt"
DEPENDENCIES_FILE = REPO_ROOT / "assets" / "dependencies.txt"
PROTECTED_REASON = ("Protected: part of the base system, kernel, firmware, drivers or desktop core. "
                    "Removing it can break booting or the desktop; use a terminal if you really mean it.")
# Why a package in a removal set blocks it; the same words as in actions/_lib.sh
REASON_SYSTEM = "protected system package"
REASON_SHELL = "login shell"
REASON_SELF = "needed by Arch Assistant"


def read_list(path: Path) -> tuple[str, ...]:
    """Non-empty lines of a list file, without # comments."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ()
    return tuple(s for line in lines if (s := line.split("#", 1)[0].strip()))


def _protected() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(fnmatch patterns, package groups) of assets/protected.txt."""
    entries = read_list(PROTECTED_FILE)
    return (tuple(e for e in entries if not e.startswith("@")),
            tuple(e.removeprefix("@") for e in entries if e.startswith("@")))


PATTERNS, GROUPS = _protected()
DEPENDENCIES = read_list(DEPENDENCIES_FILE)


def group_members(text: str) -> set[str]:
    """`pacman -Qg` output ("group package" lines): the packages of the protected groups."""
    pairs = (line.split() for line in text.splitlines())
    return {p[1] for p in pairs if len(p) == 2 and p[0] in GROUPS}


def read_group_members() -> set[str]:
    return group_members(run_readonly(["pacman", "-Qg"]) or "")


def is_system(pkg: str, members: set[str]) -> bool:
    """A protected package: listed by pattern or a member of a protected group."""
    return pkg in members or any(fnmatch(pkg, p) for p in PATTERNS)


def login_shell_packages() -> set[str]:
    """The packages owning the login shells of this user and root (zsh, bash…)."""
    shells = set()
    for get in (lambda: pwd.getpwuid(os.getuid()), lambda: pwd.getpwnam("root")):
        try:
            shells.add(get().pw_shell)
        except KeyError:
            continue
    shells.discard("")
    # exit 1 when one of the paths belongs to no package: the others are still listed
    return set((run_readonly(["pacman", "-Qoq", *sorted(shells)], codes=(0, 1)) or "").split()) if shells else set()


def protection_reasons(pkgs: Iterable[str], members: set[str], shells: set[str],
                       dependencies: Iterable[str] = DEPENDENCIES) -> dict[str, str]:
    """package → why it must not be removed, for the packages of `pkgs` that are protected."""
    deps = set(dependencies)
    reasons = {}
    for pkg in pkgs:
        if is_system(pkg, members):
            reasons[pkg] = REASON_SYSTEM
        elif pkg in shells:
            reasons[pkg] = REASON_SHELL
        elif pkg in deps:
            reasons[pkg] = REASON_SELF
    return reasons


def removal_set(names: list[str]) -> tuple[list[str], str]:
    """(every package `pacman -Rns <names>` would remove, error). Read-only: `--print` needs no root and changes
    nothing; `-n` only keeps config backups out of the way and cannot be combined with `--print`."""
    proc = run_command(["pacman", "-Rs", "--print", "--print-format", "%n", *names])
    if proc is None:
        return [], "pacman cannot be run"
    if proc.returncode != 0:
        lines = proc.stderr.strip().splitlines()
        return [], next((line for line in lines if "error" in line.lower()), lines[0] if lines else "pacman failed")
    return proc.stdout.split(), ""


@dataclass(frozen=True)
class RemovalPlan:
    picked: tuple[tuple[str, str], ...]   # (source, name) of the Arch/AUR packages that were picked
    flatpaks: tuple[str, ...]          # Flatpak app ids (removed by flatpak, outside pacman)
    packages: tuple[str, ...] = ()     # everything pacman would remove: the picked ones first, then dependencies
    blocked: tuple[tuple[str, str], ...] = ()   # (package, reason) that make the removal impossible
    error: str = ""                    # pacman could not resolve the removal

    @property
    def names(self) -> tuple[str, ...]:
        """The picked Arch/AUR packages as pacman takes them, without their source."""
        return tuple(name for _source, name in self.picked)

    @property
    def dependencies(self) -> tuple[str, ...]:
        """Packages removed only because nothing else needs them any more."""
        return tuple(p for p in self.packages if p not in self.names)

    @property
    def problem(self) -> str:
        """Why the removal must not run; "" when it may."""
        if self.error:
            return self.error
        return ("blocked: " + ", ".join(f"{p} ({why})" for p, why in self.blocked)) if self.blocked else ""


def removal_plan(args: Iterable[str]) -> RemovalPlan:
    """The plan for actions/uninstall.sh args (package_arg format): the real removal set and what blocks it.
    Runs pacman: call it off the GUI thread."""
    split = [split_arg(a) for a in args]
    picked = tuple((source, name) for source, name in split if source != "flatpak")
    flatpaks = tuple(name for source, name in split if source == "flatpak")
    plan = RemovalPlan(picked, flatpaks)
    if not plan.names:
        return plan
    removed, error = removal_set(list(plan.names))
    if error:
        return replace(plan, error=error)
    ordered = (*plan.names, *sorted(p for p in removed if p not in plan.names))
    reasons = protection_reasons(ordered, read_group_members(), login_shell_packages())
    return replace(plan, packages=ordered, blocked=tuple(reasons.items()))
