# Arch Assistant

A small tray assistant for Arch Linux + KDE Plasma. It starts with your session, runs a set of
deterministic check scripts and only speaks up when there is something to decide: pending updates
(critical packages named, the rest counted), unhealthy units, disk space, core dumps, old JetBrains
IDE versions, package caches, orphans, unsigned UKI after a kernel update, and so on. Each finding
comes with an action you approve with one click in the notification (**Yes / Yes, automatically
next time / No**) or later from the tray menu. Every action reports its result in a popup.

The notification text is a plain template built from the new findings; nothing leaves the machine
except the update and news checks themselves.

## Install

```sh
git clone https://github.com/JakubKrhovjak/arch-assistant ~/projects/arch-assistant
~/projects/arch-assistant/install.sh          # --dry-run shows the steps, --no-octopi keeps Octopi
```

The installer needs `sudo` (packages, removing `octopi`). It creates a venv in
`~/.local/share/arch-assistant/venv`, links `~/.local/bin/arch-assistant`, adds an XDG autostart
entry and copies the example config. Root for the actions comes from the existing passwordless
`sudo` on this machine; every privileged command uses an absolute path and fixed arguments.

## Configuration

`~/.config/arch-assistant/config.toml` (see `assets/config.example.toml` for every key):

| Key | Meaning | Default |
|---|---|---|
| `interval_hours` | re-check period while running | 6 |
| `critical_packages` | patterns named in update notifications | kernel, systemd, Plasma, Mesa, qemu, libvirt, docker, k3s |
| `terminal_progress` | show the progress of long actions you start (update, mirrors, package cache, orphans, removing, installing and uninstalling packages) in a Ghostty window | `true` |
| `[thresholds]` | disk %, ESP MB, cache sizes, journal errors, unused days, hog limits, mirror age, update count, re-report days | see example |

Clicking **No** snoozes that finding for `snooze_days` (default 7): no notification and no menu entry, even if the finding changes in the meantime. When the snooze runs out and the finding still applies, it is reported once more. Critical findings (unsigned UKI, failed Secure Boot check) are never snoozed.

Action modes are in `~/.config/arch-assistant/actions.toml` as `id = "ask" | "auto" | "never"`;
the tray writes `auto` when you click "Yes, automatically next time". `update`, `install`, `uninstall`,
`remove-package`, `remove-driver-packages` and `pacdiff` never run unattended, nor does `restart-unit` for system units.

### Packages window

Tray menu "Packages…" opens one window with three tabs (the update notification, its menu entry and its card in the
pending actions open it on **Updates**):

- **Updates** – every pending package with its installed and new version and its source (Arch, AUR, Flatpak), with
  search, a source filter and Refresh. Arch repo packages are updated together (partial upgrades are not supported),
  AUR packages and Flatpaks can be picked one by one. The tab warns when Arch news reports manual intervention for a
  pending package, or when `qemu`/`libvirt` would be updated while a VM is running. A dimmed line shows the last
  Timeshift snapshot (`sudo -n timeshift --list`) and whether timeshift-autosnap will take a new one before the
  upgrade (it skips it within `minHoursBetweenSnapshots` of its last one, read from `/etc/timeshift-autosnap.conf`).
- **Installed** – explicitly installed Arch packages (`pacman -Qe` minus foreign), AUR packages (`pacman -Qm`) and
  Flatpak apps, grouped into **Apps** (a visible `.desktop` application; Flatpaks always), **Utilities** (CLI tools,
  libraries, fonts…) and **System** (base, kernels, firmware, microcode, drivers, Mesa/Vulkan, systemd, boot and disk
  tools, SDDM, PipeWire, NetworkManager, the `plasma`/`kf6`/`qt6`/`xorg-drivers` groups; see
  `arch_assistant/inventory.py`). System rows are protected and cannot be checked. Checked rows are removed by
  `uninstall` (`pacman -Rns`, `flatpak uninstall`) after a confirmation listing them.
- **Browse** – one search (debounced, at least 2 characters, Enter searches at once) over the Arch repos
  (`pacman -Ss`), the AUR (RPC v5, needs the network) and Flathub (`flatpak search`), sorted by relevance (exact name,
  then name containing the term; AUR by popularity). Installed results are marked and cannot be picked; checked ones
  are installed by `install` after a confirmation. AUR packages are user-made and built from their PKGBUILD.

## Checks and actions

| Check | Action |
|---|---|
| `updates`, `arch-news` | `update [repo] [aur:<name>…] [flatpak:<id>…]` (no args: pacman -Syu, yay -Sua, flatpak update; then `secure-boot` and `reboot-needed` run) |
| `failed-units` | `restart-unit <system\|user> <unit>` |
| `coredumps` | `clean-coredumps` |
| `pacnew` | `pacdiff` (opens a terminal) |
| `mirrors` | `refresh-mirrors` |
| `pkg-cache` | `clean-pkg-cache` (paccache -rk2, yay -Sc) |
| `orphans` | `remove-orphans` |
| `jetbrains-old` | `clean-jetbrains-old <Name2025.2 …>`: only the plugins (`~/.local/share/JetBrains`) and caches (`~/.cache/JetBrains`) of a version superseded by a newer one that already has its own settings directory; settings in `~/.config/JetBrains`, backups (`*-backup`, `*.pred-*`) and Toolbox-installed versions (`dataDirectoryName`) are always kept |
| `localsend` | `restart-localsend` (reset-failed + restart of the `localsend.service` user unit; nothing reported when the unit is not installed) |
| `toolbox-cache` | `clean-toolbox-cache` (`~/.cache/JetBrains/Toolbox/temp` and `download`) |
| `user-cache` | `clean-user-cache <name …>` (safe list only; safe caches are also offered when together above the limit) |
| `unused-apps` | `remove-package <pkg>` |
| (Installed tab) | `uninstall pkg:<name>… flatpak:<id>…` (pacman -Rns; system Flatpaks via sudo, user ones as the user) |
| (Browse tab) | `install repo:<name>… aur:<name>… flatpak:<id>…` (pacman -S --needed, yay -S --aur --needed, flatpak install from Flathub system-wide) |
| `hardware-drivers` | `remove-driver-packages <pkg …>` (GPU / Wi-Fi drivers for absent hardware) |
| `disk-space`, `journal-errors`, `reboot-needed`, `secure-boot`, `timeshift`, `clocksource`, `vuln-packages`, `resource-hogs`, `docker-vm` | report only |

A check is a script in `checks/` that prints one JSON object (`id`, `severity`, `title`, `summary`,
`items`, `actions`), reads thresholds from `AA_*` environment variables and exits non-zero only when
it crashed. An action is a script in `actions/` that gets the action's args, prints a short result on
stdout (first lines go to the popup) and details on stderr (log).

Long actions started by a click (`TERMINAL_ACTIONS` in `arch_assistant/actions.py`) run in a Ghostty
window titled "Arch Assistant: <action>": the stderr output (pacman, yay, flatpak) is shown live, the
short result at the end, and the window stays open until Enter. The tray still gets the same result
(popup, menu, log) as soon as the action ends; closing the window early stops the action and counts as
a failure. Automatic actions, `arch-assistant act`, `terminal_progress = false` and a session without
Ghostty or a display run in the background as before. See
`docs/superpowers/specs/2026-10-17-arch-assistant-design.md` for the full contract.

## Debugging

```sh
arch-assistant check                 # run every check, print findings
arch-assistant check updates --json  # one check, raw JSON
arch-assistant act clean-coredumps   # run one action
arch-assistant once                  # one full cycle without GUI (auto actions do run)
tail -f ~/.local/state/arch-assistant/log/assistant.log
```

State lives in `~/.local/state/arch-assistant/state.json` (what was reported/declined); delete it to
make everything "new" again. Tests: `~/.local/share/arch-assistant/venv/bin/python -m pytest` plus
`shellcheck checks/*.sh actions/*.sh install.sh`.
