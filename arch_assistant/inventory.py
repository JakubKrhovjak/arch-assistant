"""What is installed: explicit Arch packages, AUR packages and Flatpak apps, grouped for the Installed tab. No Qt here.

Apps ship a visible .desktop application (Flatpak apps always do), System packages are the base system, kernel,
firmware, drivers and desktop core (protected: the window never offers to uninstall them), everything else is a
Utility (CLI tools, libraries, fonts and the like).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

APPLICATIONS_DIR = "/usr/share/applications/"
COMMAND_TIMEOUT = 60
CATEGORIES = ("apps", "utilities", "system")

# Packages whose removal can leave the machine unbootable or without a desktop (fnmatch patterns)
SYSTEM_PATTERNS = (
    "base", "base-devel", "linux", "linux-*", "*-firmware", "*-firmware-*", "*-ucode", "*-dkms", "*-dkms-*",
    "mesa", "lib32-mesa", "vulkan-*", "lib32-vulkan-*", "xf86-*", "xorg-server", "xorg-xwayland",
    "systemd", "systemd-*", "glibc", "glibc-*", "bash", "sudo", "pacman", "archlinux-keyring", "mkinitcpio",
    "grub", "efibootmgr", "sbctl", "refind", "cryptsetup", "lvm2", "device-mapper", "mdadm", "btrfs-progs",
    "e2fsprogs", "dosfstools", "sddm", "sddm-kcm", "plasma-meta", "plasma-desktop", "plasma-workspace", "kwin",
    "networkmanager", "wpa_supplicant", "iwd", "pipewire", "pipewire-*", "wireplumber",
    "xdg-desktop-portal", "xdg-desktop-portal-*", "yay", "yay-bin", "paru", "paru-bin",
)
# Every member of these package groups counts as System as well
SYSTEM_GROUPS = ("plasma", "kf6", "qt6", "xorg-drivers")
PROTECTED_REASON = ("Protected: part of the base system, kernel, firmware, drivers or desktop core. "
                    "Removing it can break booting or the desktop; use a terminal if you really mean it.")


@dataclass(frozen=True)
class Installed:
    name: str            # display name: the .desktop Name= or the package name
    id: str              # package name or Flatpak app id
    version: str
    source: str          # repo | aur | flatpak
    category: str        # apps | utilities | system
    icon: str = ""       # icon theme name or absolute path from the .desktop file / the Flatpak app id
    installation: str = ""   # Flatpak: system | user

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.id)

    @property
    def protected(self) -> bool:
        return self.category == "system"

    @property
    def uninstall_arg(self) -> str:
        """Argument of actions/uninstall.sh."""
        return f"flatpak:{self.id}" if self.source == "flatpak" else f"pkg:{self.id}"


@dataclass
class Inventory:
    rows: list[Installed] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)   # sources that could not be read


def _run(cmd: list[str], codes: tuple[int, ...] = (0,)) -> str | None:
    """stdout of a read-only command; None when it is missing or fails."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode in codes else None


def _name_versions(text: str) -> dict[str, str]:
    """`pacman -Q` output: name → version."""
    pairs = (line.split(None, 1) for line in text.splitlines() if line.strip())
    return {p[0]: (p[1].strip() if len(p) > 1 else "") for p in pairs}


def parse_desktop(text: str) -> dict[str, str] | None:
    """Name and Icon of a visible application entry; None for hidden entries and anything but Type=Application."""
    section, keys = None, {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
            continue
        if section != "[Desktop Entry]" or "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        keys.setdefault(k.strip(), v.strip())
    visible = keys.get("Type") == "Application" and all(keys.get(k, "").lower() != "true" for k in ("NoDisplay", "Hidden"))
    return {"name": keys.get("Name", ""), "icon": keys.get("Icon", "")} if visible and keys.get("Name") else None


def _desktop_files(text: str) -> dict[str, list[str]]:
    """`pacman -Ql` output: package → its .desktop files under /usr/share/applications."""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        pkg, _, path = line.partition(" ")
        if path.startswith(APPLICATIONS_DIR) and path.endswith(".desktop"):
            out.setdefault(pkg, []).append(path)
    return out


def _read(path: str) -> str:
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def best_entry(pkg: str, paths: list[str], read=_read) -> dict[str, str] | None:
    """The package's main application: the entry named after the package, else the shortest name among the
    entries that mention it (libreoffice-fresh → "LibreOffice", not "LibreOffice Writer"). A package with several
    unrelated entries (plasma-desktop: settings modules, the emoji picker) keeps its package name."""
    entries = [dict(e, stem=Path(p).stem.lower()) for p in sorted(paths) if (e := parse_desktop(read(p)))]
    base = pkg.lower()
    for suffix in ("-bin", "-git", "-fresh", "-still"):
        base = base.removesuffix(suffix)

    def related(e: dict) -> bool:
        # proton-vpn-gtk-app ships proton.vpn.app.gtk.desktop: every word of the name, in any order
        return base in e["stem"] or set(base.split("-")) <= set(re.split(r"[.\-_]", e["stem"]))

    ranked = sorted(entries, key=lambda e: (e["stem"].rsplit(".", 1)[-1] != base, not related(e), len(e["name"])))
    if not ranked:
        return None
    best = ranked[0]
    return best if related(best) or len(entries) == 1 else dict(best, name=pkg)


def _groups(text: str) -> set[str]:
    """`pacman -Qg` output ("group package" lines): the packages of the System groups."""
    pairs = (line.split() for line in text.splitlines())
    return {p[1] for p in pairs if len(p) == 2 and p[0] in SYSTEM_GROUPS}


def is_system(pkg: str, group_members: set[str]) -> bool:
    return pkg in group_members or any(fnmatch(pkg, p) for p in SYSTEM_PATTERNS)


def _packages(inv: Inventory, read) -> None:
    # pacman -Qm exits 1 when there are no foreign packages at all
    explicit, foreign = _run(["pacman", "-Qe"]), _run(["pacman", "-Qm"], codes=(0, 1))
    if explicit is None or foreign is None:
        inv.errors.append("pacman")
        return
    aur = _name_versions(foreign)
    repo = {k: v for k, v in _name_versions(explicit).items() if k not in aur}
    names = sorted({*repo, *aur})
    files = _desktop_files(_run(["pacman", "-Ql", *names]) or "") if names else {}
    members = _groups(_run(["pacman", "-Qg"]) or "")
    for source, pkgs in (("repo", repo), ("aur", aur)):
        for pkg, version in pkgs.items():
            entry = best_entry(pkg, files.get(pkg, []), read)
            category = "system" if is_system(pkg, members) else ("apps" if entry else "utilities")
            inv.rows.append(Installed(entry["name"] if entry else pkg, pkg, version, source, category,
                                      entry["icon"] if entry else ""))


def _flatpaks(inv: Inventory) -> None:
    text = _run(["flatpak", "list", "--app", "--columns=application,name,version,installation"])
    if text is None:
        return   # no flatpak or no remotes: nothing to list, not an error worth showing
    for line in text.splitlines():
        cols = line.split("\t")
        if not cols[0].strip():
            continue
        app_id, name, version, installation = (cols + ["", "", ""])[:4]
        inv.rows.append(Installed(name.strip() or app_id, app_id.strip(), version.strip(), "flatpak", "apps",
                                  app_id.strip(), installation.strip() or "system"))


def sort_key(row: Installed) -> tuple:
    return (CATEGORIES.index(row.category), row.name.lower(), row.id)


def collect(read=_read) -> Inventory:
    """Everything installed, sorted by category then name. Runs pacman and flatpak: call it off the GUI thread."""
    inv = Inventory()
    _packages(inv, read)
    _flatpaks(inv)
    inv.rows.sort(key=sort_key)
    return inv
