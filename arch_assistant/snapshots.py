"""The last Timeshift snapshot and whether timeshift-autosnap will take a new one before the next upgrade. No Qt here.

`timeshift --list` needs root (the snapshot subvolume is only mounted while timeshift runs); the passwordless
`sudo -n` is used, and anything that fails just means "no snapshot info".
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

AUTOSNAP_CONF = Path("/etc/timeshift-autosnap.conf")
AUTOSNAP_BIN = Path("/usr/bin/timeshift-autosnap")
AUTOSNAP_DEFAULTS = {"skipAutosnap": "false", "minHoursBetweenSnapshots": "18",
                     "snapshotDescription": "{timeshift-autosnap} {created before upgrade}"}
LIST_TIMEOUT = 60
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
TAGS = {"O": "on demand", "B": "boot", "H": "hourly", "D": "daily", "W": "weekly", "M": "monthly"}
ROW_RE = re.compile(r"^\s*\d+\s+>?\s*(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\s+([OBHDWM ]*?)\s{2,}(.*?)\s*$")
ROW_NO_DESC_RE = re.compile(r"^\s*\d+\s+>?\s*(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\s*([OBHDWM ]*)\s*$")


@dataclass(frozen=True)
class Snapshot:
    at: datetime
    tags: str
    description: str


@dataclass(frozen=True)
class AutosnapConfig:
    skip: bool
    min_hours: int
    description: str


def parse_list(text: str) -> list[Snapshot]:
    """Rows of `timeshift --list`, oldest first."""
    out = []
    for line in text.splitlines():
        m = ROW_RE.match(line) or ROW_NO_DESC_RE.match(line)
        if not m:
            continue
        desc = m.group(3) if m.re is ROW_RE else ""
        out.append(Snapshot(datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S"), m.group(2).strip(), desc.strip()))
    return sorted(out, key=lambda s: s.at)


def parse_autosnap_conf(text: str) -> AutosnapConfig:
    """The values timeshift-autosnap itself reads (last non-comment `key=value` wins, invalid ones fall back)."""
    values = dict(AUTOSNAP_DEFAULTS)
    for line in text.splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        k, v = (s.strip() for s in line.split("=", 1))
        if k in values and v:
            values[k] = v
    hours = values["minHoursBetweenSnapshots"]
    return AutosnapConfig(values["skipAutosnap"] == "true",
                          int(hours) if hours.isdigit() else int(AUTOSNAP_DEFAULTS["minHoursBetweenSnapshots"]),
                          values["snapshotDescription"])


def format_when(at: datetime, now: datetime) -> str:
    """today 07:31 / yesterday 22:10 / tomorrow 01:31 / Nov 3 04:57 (English whatever the locale)."""
    day = (at.date() - now.date()).days
    prefix = {0: "today", -1: "yesterday", 1: "tomorrow"}.get(day, f"{MONTHS[at.month - 1]} {at.day}")
    return f"{prefix} {at:%H:%M}"


def describe(s: Snapshot, autosnap_description: str) -> str:
    if s.description == autosnap_description:
        return "before upgrade"
    if s.description:
        return s.description if len(s.description) <= 40 else s.description[:39] + "…"
    return ", ".join(TAGS[t] for t in s.tags.split() if t in TAGS)


def snapshot_lines(snapshots: list[Snapshot], conf: AutosnapConfig | None, now: datetime) -> list[str]:
    """The dimmed lines of the Updates tab: the last snapshot, then what timeshift-autosnap will do."""
    if not snapshots:
        return ["No Timeshift snapshot yet"]
    last = snapshots[-1]
    what = describe(last, conf.description if conf else AUTOSNAP_DEFAULTS["snapshotDescription"])
    lines = [f"Last snapshot: {format_when(last.at, now)}" + (f" ({what})" if what else "")]
    if conf is None:
        return lines
    if conf.skip:
        return lines + ["timeshift-autosnap is switched off (skipAutosnap), no snapshot before the upgrade"]
    auto = [s for s in snapshots if s.description == conf.description]
    # same arithmetic as timeshift-autosnap: whole hours since its last snapshot
    if auto and (now - auto[-1].at) // timedelta(hours=1) < conf.min_hours:
        until = auto[-1].at + timedelta(hours=conf.min_hours)
        return lines + [f"No new snapshot before the upgrade: timeshift-autosnap made one {format_when(auto[-1].at, now)} "
                        f"and waits {conf.min_hours} h (until {format_when(until, now)})"]
    return lines + ["timeshift-autosnap takes a new snapshot before the upgrade"]


def snapshot_info(now: datetime | None = None, conf_path: Path = AUTOSNAP_CONF,
                  autosnap_bin: Path = AUTOSNAP_BIN) -> list[str] | None:
    """The lines for the Updates tab, or None when timeshift cannot be read. Slow (mounts): not on the GUI thread."""
    try:
        proc = subprocess.run(["sudo", "-n", "/usr/bin/timeshift", "--list"], capture_output=True, text=True,
                              timeout=LIST_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    conf = None
    if conf_path.is_file() or autosnap_bin.exists():
        try:
            conf = parse_autosnap_conf(conf_path.read_text())
        except OSError:
            conf = parse_autosnap_conf("")
    return snapshot_lines(parse_list(proc.stdout), conf, now or datetime.now())
