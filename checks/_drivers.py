"""Shared helper: GPU / Wi-Fi driver packages and the hardware vendor each one is for."""
from __future__ import annotations

import os
import re
import subprocess
from fnmatch import fnmatch

GPU_CLASSES = ("0300", "0302", "0380")
NET_CLASSES = ("0200", "0280")
VENDOR_IDS = {"8086": "intel", "10de": "nvidia", "1002": "amd", "14e4": "broadcom"}
VENDOR_NAMES = (("intel", "intel"), ("nvidia", "nvidia"), ("advanced micro devices", "amd"), ("amd/ati", "amd"),
                ("ati ", "amd"), ("broadcom", "broadcom"))
GPU_TEXT = ("vga compatible", "3d controller", "display controller")
NET_TEXT = ("network controller", "ethernet controller")

# hardware key -> package patterns that are useless without it. linux-firmware-intel is left out on purpose:
# it also carries the Intel Wi-Fi / Bluetooth firmware used on AMD boards.
PACKAGES = {
    "intel-gpu": ("libva-intel-driver", "lib32-libva-intel-driver", "intel-media-driver", "vulkan-intel",
                  "lib32-vulkan-intel", "xf86-video-intel"),
    "nvidia-gpu": ("vulkan-nouveau", "lib32-vulkan-nouveau", "xf86-video-nouveau", "nvidia*", "lib32-nvidia*",
                   "linux-firmware-nvidia"),
    "ati-gpu": ("xf86-video-ati",),
    "broadcom-wifi": ("b43-fwcutter", "broadcom-wl*"),
}
LABELS = {"intel-gpu": "Intel GPU", "nvidia-gpu": "NVIDIA GPU", "ati-gpu": "old ATI GPU (amdgpu in use)",
          "broadcom-wifi": "Broadcom Wi-Fi"}


def vendor_of(line: str) -> str | None:
    ids = re.findall(r"\[([0-9a-f]{4}):[0-9a-f]{4}\]", line.lower())
    if ids:
        return VENDOR_IDS.get(ids[-1])
    low = line.lower()
    return next((v for needle, v in VENDOR_NAMES if needle in low), None)


def kind_of(line: str) -> str | None:
    low = line.lower()
    codes = re.findall(r"\[([0-9a-f]{4})\]", low)
    if any(c in GPU_CLASSES for c in codes) or any(t in low for t in GPU_TEXT):
        return "gpu"
    if any(c in NET_CLASSES for c in codes) or any(t in low for t in NET_TEXT):
        return "net"
    return None


def detect_hardware(lspci_out: str) -> tuple[set[str], bool, bool]:
    """(present hardware keys, any GPU seen, any network device seen)."""
    present: set[str] = set()
    gpu_seen = net_seen = False
    for line in lspci_out.splitlines():
        kind, vendor = kind_of(line), vendor_of(line)
        if kind == "gpu":
            gpu_seen = True
            if vendor:
                present.add(f"{vendor}-gpu")
        elif kind == "net":
            net_seen = True
            if vendor == "broadcom" and ("[0280]" in line or "wireless" in line.lower() or "network controller" in line.lower()):
                present.add("broadcom-wifi")
    return present, gpu_seen, net_seen


def read_lspci() -> str:
    try:
        out = subprocess.run(["lspci", "-nn"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def read_installed() -> list[str]:
    try:
        out = subprocess.run(["pacman", "-Qq"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [l.strip() for l in out.stdout.splitlines() if l.strip()]


def is_driver_package(name: str) -> bool:
    return any(fnmatch(name, p) for pats in PACKAGES.values() for p in pats)


def read_required_by(pkg: str) -> list[str]:
    """Installed packages that depend on pkg (pacman -Qi, "Required By")."""
    try:
        out = subprocess.run(["pacman", "-Qi", pkg], capture_output=True, text=True, timeout=30,
                             env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return []
    for line in out.stdout.splitlines():
        if line.startswith("Required By"):
            value = line.split(":", 1)[1].strip()
            return [] if value in ("", "None") else value.split()
    return []


def stale_packages(lspci_out: str, installed: list[str], required_by=read_required_by) -> tuple[list[dict], list[dict]]:
    """(offered, blocked) driver packages for absent hardware; both empty when the hardware is unclear.

    A package that another installed package outside the offered set requires cannot be uninstalled: it is blocked.
    """
    present, gpu_seen, net_seen = detect_hardware(lspci_out)
    absent = []
    if gpu_seen:
        absent += [k for k in ("intel-gpu", "nvidia-gpu") if k not in present]
        if "amd-gpu" in present or "xf86-video-amdgpu" in installed:
            absent.append("ati-gpu")
    if net_seen and "broadcom-wifi" not in present:
        absent.append("broadcom-wifi")
    cands = [{"name": pkg, "hardware": LABELS[key]} for key in absent for pkg in installed
             if any(fnmatch(pkg, p) for p in PACKAGES[key])]
    needs = {c["name"]: required_by(c["name"]) for c in cands}
    offered, blocked = cands, []
    while True:
        names = {c["name"] for c in offered}
        stuck = [c for c in offered if any(r not in names for r in needs[c["name"]])]
        if not stuck:
            return offered, blocked
        blocked += [{**c, "required_by": [r for r in needs[c["name"]] if r not in names]} for c in stuck]
        offered = [c for c in offered if c not in stuck]
