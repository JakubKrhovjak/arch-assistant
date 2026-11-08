"""Shared fixtures: fake commands on PATH (shim) and running checks from the repo."""
from __future__ import annotations

import os
import shlex
import stat
from pathlib import Path

import pytest

from arch_assistant.checks import run_check
from arch_assistant.config import check_env, load_config
from arch_assistant.model import Finding

REPO = Path(__file__).resolve().parent.parent


class Shim:
    """Writes executable scripts into a temporary bin dir that is first on PATH."""

    def __init__(self, bin_dir: Path, monkeypatch: pytest.MonkeyPatch):
        self.bin_dir = bin_dir
        bin_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        self.calls_file = bin_dir / "calls.log"

    def cmd(self, name: str, stdout: str = "", exit_code: int = 0, stderr: str = "", script: str | None = None) -> Path:
        """A command that logs its arguments and prints fixed output; `script` = custom bash body."""
        body = script if script is not None else (
            f"printf '%s' {shlex.quote(stdout)}\n"
            + (f"printf '%s' {shlex.quote(stderr)} >&2\n" if stderr else "")
            + f"exit {exit_code}\n"
        )
        path = self.bin_dir / name
        path.write_text(f'#!/bin/bash\necho "{name} $*" >> {shlex.quote(str(self.calls_file))}\n{body}')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def sudo(self) -> Path:
        """A sudo that drops the absolute path (/usr/bin/pacman → pacman) and runs the shim of that name."""
        return self.cmd("sudo", script='cmd=$(basename "$1"); shift; exec "$cmd" "$@"\n')

    def pacman_removal(self, targets: str, groups: str = "plasma kwin\n", owners: str = "zsh\nbash\n",
                       rns_stderr: str = "") -> Path:
        """A pacman for the uninstall guard: `-Rs --print` lists `targets` (one per line), `-Qg` / `-Qgq` the
        `groups` ("group package" lines), `-Qoq` the login shell `owners`; `-Rns` fails with `rns_stderr` if given."""
        members = "".join(f"{line.split()[1]}\n" for line in groups.splitlines() if len(line.split()) == 2)
        rns = f"printf '%s\\n' {shlex.quote(rns_stderr)} >&2; exit 1" if rns_stderr else "exit 0"
        return self.cmd("pacman", script=(
            'case "$1" in\n'
            f"  -Rs) printf '%s' {shlex.quote(targets)} ;;\n"
            f"  -Qg) printf '%s' {shlex.quote(groups)} ;;\n"
            f"  -Qgq) printf '%s' {shlex.quote(members)} ;;\n"
            f"  -Qoq) printf '%s' {shlex.quote(owners)} ;;\n"
            f"  -Rns) {rns} ;;\n"
            "esac\n"))

    def calls(self) -> list[str]:
        return self.calls_file.read_text().splitlines() if self.calls_file.exists() else []


@pytest.fixture(autouse=True)
def _no_display(monkeypatch):
    """Tests never open real windows: without a display, actions run in the background, not in a terminal."""
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)


@pytest.fixture
def qapp():
    """The QApplication, offscreen: Qt tests never open real windows."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def shim(tmp_path, monkeypatch) -> Shim:
    return Shim(tmp_path / "bin", monkeypatch)


class CheckRunner:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.state_dir = tmp_path / "state"
        self.state_dir.mkdir(exist_ok=True)

    def run(self, check_id: str, env: dict[str, str] | None = None, timeout: float = 60) -> Finding:
        cfg = load_config(self.tmp_path / "cfg", self.state_dir)
        full_env = {**check_env(cfg), **(env or {})}
        scripts = [p for p in (REPO / "checks").glob(f"{check_id}.*") if p.suffix in (".sh", ".py")]
        assert scripts, f"check {check_id} does not exist in the repo"
        return run_check(scripts[0], full_env, timeout=timeout)


@pytest.fixture
def check_runner(tmp_path) -> CheckRunner:
    return CheckRunner(tmp_path)
