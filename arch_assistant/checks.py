"""Hledání a paralelní běh kontrolních skriptů (checks/<id>.sh | .py) s JSON kontraktem."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

from .model import Finding

CHECK_SUFFIXES = (".sh", ".py")


def discover_checks(checks_dir: Path, only: Iterable[str] | None = None) -> list[Path]:
    """Skripty kontrol seřazené podle jména; soubory začínající `_` jsou pomocné moduly, ne kontroly."""
    wanted = set(only) if only is not None else None
    return sorted(
        p for p in checks_dir.iterdir()
        if p.suffix in CHECK_SUFFIXES and not p.name.startswith("_")
        and (wanted is None or p.stem in wanted)
    )


def _command(script: Path) -> list[str]:
    prefix = []
    if shutil.which("nice"):
        prefix += ["nice", "-n", "10"]
    if shutil.which("ionice"):
        prefix += ["ionice", "-c", "3"]
    runner = [sys.executable, str(script)] if script.suffix == ".py" else [str(script)]
    return prefix + runner


def run_check(script: Path, env: dict[str, str], timeout: float = 60) -> Finding:
    """Spustí jednu kontrolu; cokoli jiného než validní JSON na stdout = nález „kontrola selhala“."""
    check_id = script.stem
    try:
        proc = subprocess.run(
            _command(script), capture_output=True, text=True, timeout=timeout,
            env={**os.environ, **env}, check=False,
        )
    except subprocess.TimeoutExpired:
        return Finding.failed(check_id, f"timeout po {timeout:g} s")
    except OSError as e:
        return Finding.failed(check_id, f"nelze spustit: {e}")
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"návratový kód {proc.returncode}"
        return Finding.failed(check_id, detail)
    if not proc.stdout.strip():
        return Finding.failed(check_id, "prázdný výstup" + (f"\n{proc.stderr.strip()}" if proc.stderr.strip() else ""))
    try:
        return Finding.from_json(proc.stdout)
    except ValueError as e:
        return Finding.failed(check_id, f"{e}\n{proc.stdout.strip()[:500]}")


def run_all(checks_dir: Path, env: dict[str, str], only: Iterable[str] | None = None,
            timeout: float = 60, max_workers: int = 8) -> list[Finding]:
    scripts = discover_checks(checks_dir, only)
    if not scripts:
        return []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(lambda s: run_check(s, env, timeout), scripts))
