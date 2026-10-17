"""Configuration: config.toml (thresholds, model), actions.toml (action modes), environment for checks."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Mode = Literal["ask", "auto", "never"]
MODES: tuple[str, ...] = ("ask", "auto", "never")

REPO_ROOT = Path(__file__).resolve().parent.parent

BACKENDS: tuple[str, ...] = ("claude-code", "api", "template")

DEFAULTS: dict = {
    "backend": "claude-code",
    "model": "claude-haiku-5-5",
    "interval_hours": 6,
    "api_key_file": "~/.config/zsh/secrets.zsh",
    "critical_packages": [
        "linux", "linux-lts", "linux-firmware", "systemd", "plasma-*", "kwin",
        "mesa", "qemu*", "libvirt*", "docker", "k3s", "com.google.Chrome",
    ],
    "thresholds": {
        "disk_pct": 10, "esp_mb": 30, "pkg_cache_gb": 2, "user_cache_gb": 1,
        "journal_err": 50, "unused_days": 90, "hog_cpu_pct": 50, "hog_rss_gb": 4,
        "hog_minutes": 5, "mirror_days": 7, "update_min_count": 20, "report_days": 7, "snooze_days": 7,
    },
}


@dataclass
class Config:
    backend: str
    model: str
    interval_hours: float
    api_key_file: Path
    critical_packages: list[str]
    thresholds: dict[str, float]
    actions: dict[str, Mode]
    config_dir: Path
    state_dir: Path
    checks_dir: Path
    actions_dir: Path
    config_error: str | None = None


def default_config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "arch-assistant"


def default_state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "arch-assistant"


def _read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text()) if path.is_file() else {}


def _read_toml_safe(path: Path) -> tuple[dict, str | None]:
    """(data, error): a file with a syntax error yields {} and a message instead of crashing the tray."""
    try:
        return _read_toml(path), None
    except (tomllib.TOMLDecodeError, OSError) as e:
        return {}, f"{path}: {e}"


def load_config(config_dir: Path | None = None, state_dir: Path | None = None) -> Config:
    config_dir = config_dir or default_config_dir()
    state_dir = state_dir or default_state_dir()
    raw, err_cfg = _read_toml_safe(config_dir / "config.toml")
    modes, err_modes = _read_toml_safe(config_dir / "actions.toml")
    thresholds = {**DEFAULTS["thresholds"], **raw.get("thresholds", {})}
    backend = str(raw.get("backend", DEFAULTS["backend"]))
    return Config(
        backend=backend if backend in BACKENDS else DEFAULTS["backend"],
        model=str(raw.get("model", DEFAULTS["model"])),
        interval_hours=float(raw.get("interval_hours", DEFAULTS["interval_hours"])),
        api_key_file=Path(raw.get("api_key_file", DEFAULTS["api_key_file"])).expanduser(),
        critical_packages=list(raw.get("critical_packages", DEFAULTS["critical_packages"])),
        thresholds=thresholds,
        actions={k: v for k, v in modes.items() if v in MODES},
        config_dir=config_dir,
        state_dir=state_dir,
        checks_dir=REPO_ROOT / "checks",
        actions_dir=REPO_ROOT / "actions",
        config_error=" ; ".join(e for e in (err_cfg, err_modes) if e) or None,
    )


def set_action_mode(config_dir: Path, action_id: str, mode: Mode) -> None:
    """Write an action mode to actions.toml (plain `id = "mode"` lines, no library needed)."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", action_id):
        raise ValueError(f"invalid action id {action_id!r}")
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "actions.toml"
    modes = {k: v for k, v in _read_toml(path).items() if v in MODES}
    modes[action_id] = mode
    lines = ["# Arch Assistant action modes: ask / auto / never (written by the app)"]
    lines += [f'{k} = "{v}"' for k, v in sorted(modes.items())]
    path.write_text("\n".join(lines) + "\n")


def check_env(cfg: Config) -> dict[str, str]:
    """AA_* environment variables for check scripts."""
    t = cfg.thresholds

    def num(x: float) -> str:
        return str(int(x)) if float(x).is_integer() else str(x)

    return {
        "AA_CRITICAL_PACKAGES": " ".join(cfg.critical_packages),
        "AA_DISK_PCT": num(t["disk_pct"]),
        "AA_ESP_MB": num(t["esp_mb"]),
        "AA_PKG_CACHE_GB": num(t["pkg_cache_gb"]),
        "AA_USER_CACHE_GB": num(t["user_cache_gb"]),
        "AA_JOURNAL_ERR": num(t["journal_err"]),
        "AA_UNUSED_DAYS": num(t["unused_days"]),
        "AA_HOG_CPU_PCT": num(t["hog_cpu_pct"]),
        "AA_HOG_RSS_GB": num(t["hog_rss_gb"]),
        "AA_HOG_MINUTES": num(t["hog_minutes"]),
        "AA_MIRROR_DAYS": num(t["mirror_days"]),
        "AA_STATE_DIR": str(cfg.state_dir),
    }
