#!/bin/bash
# Install Arch Assistant for the current user. Idempotent; needs sudo for packages.
# Usage: ./install.sh [--dry-run] [--no-octopi]
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
venv="$HOME/.local/share/arch-assistant/venv"
bin_link="$HOME/.local/bin/arch-assistant"
autostart="$HOME/.config/autostart/arch-assistant.desktop"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/arch-assistant"
dry_run=0
remove_octopi=1
for arg in "$@"; do
    case "$arg" in
        --dry-run) dry_run=1 ;;
        --no-octopi) remove_octopi=0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

step() { echo "$1. $2"; }
run() { [[ $dry_run -eq 1 ]] || "$@"; }

step 1 "Install system packages: pacman-contrib arch-audit python-jeepney shellcheck (sudo)"
run sudo pacman -S --needed --noconfirm pacman-contrib arch-audit python-jeepney shellcheck

step 2 "Create venv $venv (system site packages for PySide6) and install the package"
if [[ $dry_run -eq 0 ]]; then
    [[ -x "$venv/bin/python" ]] || python3 -m venv --system-site-packages "$venv"
    "$venv/bin/pip" install --quiet --upgrade pip
    "$venv/bin/pip" install --quiet -e "$here"
fi

step 3 "Link $bin_link -> $here/bin/arch-assistant"
if [[ $dry_run -eq 0 ]]; then
    mkdir -p "$(dirname "$bin_link")"
    ln -sfn "$here/bin/arch-assistant" "$bin_link"
fi

step 4 "Autostart entry $autostart"
if [[ $dry_run -eq 0 ]]; then
    mkdir -p "$(dirname "$autostart")"
    sed -e "s|@BIN@|$here/bin/arch-assistant|g" -e "s|@ICON@|$here/assets/tray.svg|g" \
        "$here/assets/arch-assistant.desktop" > "$autostart"
fi

step_4b_apps="$HOME/.local/share/applications/arch-assistant.desktop"
echo "-  Desktop entry $step_4b_apps (application menu entry; also gives the windows their icon on Wayland)"
if [[ $dry_run -eq 0 ]]; then
    mkdir -p "$(dirname "$step_4b_apps")"
    sed -e "s|@BIN@|$here/bin/arch-assistant|g" -e "s|@ICON@|$here/assets/tray.svg|g" -e "s|^NoDisplay=true|NoDisplay=false|" \
        "$here/assets/arch-assistant.desktop" > "$step_4b_apps"
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi

echo "-  KWin rule: the assistant's windows never appear in the task bar (tray app)"
if [[ $dry_run -eq 0 ]] && command -v kwriteconfig6 >/dev/null 2>&1; then
    rules=$(kreadconfig6 --file kwinrulesrc --group General --key rules 2>/dev/null || true)
    if [[ ",$rules," != *",arch-assistant-tray,"* ]]; then
        kwriteconfig6 --file kwinrulesrc --group General --key rules "${rules:+$rules,}arch-assistant-tray"
    fi
    kwriteconfig6 --file kwinrulesrc --group arch-assistant-tray --key Description "Arch Assistant: tray only, not in the task bar"
    kwriteconfig6 --file kwinrulesrc --group arch-assistant-tray --key wmclass arch-assistant
    kwriteconfig6 --file kwinrulesrc --group arch-assistant-tray --key wmclassmatch 1
    kwriteconfig6 --file kwinrulesrc --group arch-assistant-tray --key skiptaskbar true
    kwriteconfig6 --file kwinrulesrc --group arch-assistant-tray --key skiptaskbarrule 2
    qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure >/dev/null 2>&1 || true
fi

step 5 "Config $config_dir/config.toml from assets/config.example.toml (only if missing)"
if [[ $dry_run -eq 0 ]]; then
    mkdir -p "$config_dir"
    [[ -f "$config_dir/config.toml" ]] || cp "$here/assets/config.example.toml" "$config_dir/config.toml"
fi

if [[ $remove_octopi -eq 1 ]]; then
    step 6 "Replace octopi-notifier: stop it, remove its autostart entry, uninstall octopi (sudo)"
    if [[ $dry_run -eq 0 ]]; then
        pkill -x octopi-notifier 2>/dev/null || true
        rm -f "$HOME/.config/autostart/octopi-notifier.desktop"
        if pacman -Q octopi >/dev/null 2>&1; then
            sudo pacman -Rns --noconfirm octopi
        fi
    fi
else
    echo "-  (octopi left in place: --no-octopi)"
fi

step 7 "Start arch-assistant in the background if it is not running"
if [[ $dry_run -eq 0 ]]; then
    if ! pgrep -f -- "-m arch_assistant$" >/dev/null 2>&1; then
        setsid "$here/bin/arch-assistant" >/dev/null 2>&1 < /dev/null &
        disown || true
        echo "   started"
    else
        echo "   already running"
    fi
fi
