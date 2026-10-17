# Arch Assistant

A small tray assistant for Arch Linux + KDE Plasma. It starts with your session, runs a set of
deterministic check scripts and only speaks up when there is something to decide: pending updates
(critical packages named, the rest counted), unhealthy units, disk space, core dumps, old JetBrains
IDE versions, package caches, orphans, unsigned UKI after a kernel update, and so on. Each finding
comes with an action you approve with one click in the notification (**Yes / Yes, automatically
next time / No**) or later from the tray menu. Every action reports its result in a popup.

Claude (Haiku 5.5 by default) is used for one thing only: turning the findings into a short
notification text, once per run and only when there is something new. With no API key the same
text comes from a template. Detection and actions never involve the model.

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
| `model` | Claude model for the summary | `claude-haiku-5-5` |
| `interval_hours` | re-check period while running | 6 |
| `api_key_file` | shell file with `ANTHROPIC_API_KEY=` | `~/.config/zsh/secrets.zsh` |
| `critical_packages` | patterns named in update notifications | kernel, systemd, Plasma, Mesa, qemu, libvirt, docker, k3s |
| `[thresholds]` | disk %, ESP MB, cache sizes, journal errors, unused days, hog limits, mirror age, update count, re-report days | see example |

Clicking **No** snoozes that finding for `snooze_days` (default 7): no notification and no menu entry, even if the finding changes in the meantime. When the snooze runs out and the finding still applies, it is reported once more. Critical findings (unsigned UKI, failed Secure Boot check) are never snoozed.

Action modes are in `~/.config/arch-assistant/actions.toml` as `id = "ask" | "auto" | "never"`;
the tray writes `auto` when you click "Yes, automatically next time". `remove-package` and `pacdiff`
never run unattended, nor does `restart-unit` for system units. An automatic `update` still asks when
Arch news reports manual intervention for a pending package, or when `qemu`/`libvirt` would be
updated while a VM is running.

## Checks and actions

| Check | Action |
|---|---|
| `updates`, `arch-news` | `update` (pacman -Syu, yay -Sua; then `secure-boot` and `reboot-needed` run) |
| `failed-units` | `restart-unit <system\|user> <unit>` |
| `coredumps` | `clean-coredumps` |
| `pacnew` | `pacdiff` (opens a terminal) |
| `mirrors` | `refresh-mirrors` |
| `pkg-cache` | `clean-pkg-cache` (paccache -rk2, yay -Sc) |
| `orphans` | `remove-orphans` |
| `jetbrains-old` | `clean-jetbrains-old <Name2025.2 …>` (never the current version) |
| `user-cache` | `clean-user-cache <name …>` (safe list only) |
| `unused-apps` | `remove-package <pkg>` |
| `disk-space`, `journal-errors`, `reboot-needed`, `secure-boot`, `timeshift`, `clocksource`, `vuln-packages`, `resource-hogs`, `docker-vm` | report only |

A check is a script in `checks/` that prints one JSON object (`id`, `severity`, `title`, `summary`,
`items`, `actions`), reads thresholds from `AA_*` environment variables and exits non-zero only when
it crashed. An action is a script in `actions/` that gets the action's args, prints a short result on
stdout (first lines go to the popup) and details on stderr (log). See
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
