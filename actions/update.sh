#!/bin/bash
# Action: full system update (repo via pacman, then AUR via yay, then Flatpak apps and runtimes).
# stdout: "Updated N packages (repo), M (AUR) and K Flatpaks" [+ "New .pacnew files: K"], or "Update failed: <reason>".
# stderr: full pacman/yay output. Exit 1 if pacman fails (yay is skipped then).
# Env: AA_PACMAN_LOG (default /var/log/pacman.log), AA_UPDATE_SINCE (ISO timestamp, default now).
set -uo pipefail

pacman_log="${AA_PACMAN_LOG:-/var/log/pacman.log}"
since="${AA_UPDATE_SINCE:-$(date +%Y-%m-%dT%H:%M:%S)}"

repo_count=$(pacman -Qu 2>/dev/null | grep -c . || true)
aur_count=0
have_yay=0
if command -v yay >/dev/null 2>&1; then
    have_yay=1
    aur_count=$(yay -Qua 2>/dev/null | grep -c . || true)
fi
flatpak_count=0
have_flatpak=0
if command -v flatpak >/dev/null 2>&1; then
    have_flatpak=1
    flatpak_count=$(flatpak remote-ls --updates --columns=application 2>/dev/null | grep -c . || true)
fi

err_file=$(mktemp)
trap 'rm -f "$err_file"' EXIT

if ! sudo /usr/bin/pacman -Syu --noconfirm 2>"$err_file" 1>&2; then
    cat "$err_file" >&2
    reason=$(grep -m1 -i "error" "$err_file" || head -n1 "$err_file")
    echo "Update failed: ${reason:-pacman exited with an error}"
    exit 1
fi
cat "$err_file" >&2

if [[ $have_yay -eq 1 ]]; then
    if ! yay -Sua --noconfirm 1>&2 2>"$err_file"; then
        cat "$err_file" >&2
        reason=$(grep -m1 -i "error" "$err_file" || head -n1 "$err_file")
        echo "Updated $repo_count packages (repo); AUR update failed: ${reason:-yay exited with an error}"
        exit 1
    fi
fi

if [[ $have_flatpak -eq 1 && "$flatpak_count" -gt 0 ]]; then
    # system-wide Flatpaks: sudo instead of a polkit prompt that cannot be answered in the background
    if ! sudo /usr/bin/flatpak update -y --noninteractive 1>&2 2>"$err_file"; then
        cat "$err_file" >&2
        reason=$(grep -m1 -i "error" "$err_file" || head -n1 "$err_file")
        echo "Updated $repo_count packages (repo), $aur_count (AUR); Flatpak update failed: ${reason:-flatpak exited with an error}"
        exit 1
    fi
fi

echo "Updated $repo_count packages (repo), $aur_count (AUR) and $flatpak_count Flatpaks"

if [[ -r "$pacman_log" ]]; then
    pacnew=$(awk -v since="$since" '
        /installed as .*\.pacnew/ { ts = substr($1, 2, 19); if (ts >= since) n++ }
        END { print n + 0 }' "$pacman_log")
    if [[ "$pacnew" -gt 0 ]]; then
        echo "New .pacnew files: $pacnew"
    fi
fi
