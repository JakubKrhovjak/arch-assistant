#!/bin/bash
# Action: system update (repo via pacman, then AUR via yay, then Flatpak apps and runtimes).
# Args: none = update everything. Otherwise any of (the format of actions/_lib.sh parse_args)
#   repo:<name>     any repo package means all of them (pacman -Syu; partial repo upgrades are unsupported on Arch)
#   aur:<name>      this AUR package (repeatable)
#   flatpak:<id>    this Flatpak (repeatable)
# Flatpaks of the system installation are updated via sudo, those of the user installation as the user.
# stdout: "Updated N packages (repo), M (AUR) and K Flatpaks" [+ "New .pacnew files: K"], or "Update failed: <reason>".
# stderr: full pacman/yay output. Exit 1 if pacman fails (yay is skipped then), 2 on a bad argument.
# Env: AA_PACMAN_LOG (default /var/log/pacman.log), AA_UPDATE_SINCE (ISO timestamp, default now).
set -uo pipefail
# shellcheck source-path=SCRIPTDIR source=_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

pacman_log="${AA_PACMAN_LOG:-/var/log/pacman.log}"
since="${AA_UPDATE_SINCE:-$(date +%Y-%m-%dT%H:%M:%S)}"

parse_args "Update" "" "$@"
selective=0
want_repo=1
if [[ $# -gt 0 ]]; then
    selective=1
    want_repo=$(( ${#repo_names[@]} > 0 ))
fi

repo_count=0
if [[ $want_repo -eq 1 ]]; then
    repo_count=$(pacman -Qu 2>/dev/null | grep -c . || true)
fi
aur_count=${#aur_names[@]}
have_yay=0
if command -v yay >/dev/null 2>&1; then
    have_yay=1
    if [[ $selective -eq 0 ]]; then
        aur_count=$(yay -Qua 2>/dev/null | grep -c . || true)
    fi
fi
flatpak_count=${#flatpak_ids[@]}
have_flatpak=0
if command -v flatpak >/dev/null 2>&1; then
    have_flatpak=1
    if [[ $selective -eq 0 ]]; then
        flatpak_count=$(flatpak remote-ls --updates --columns=application 2>/dev/null | grep -c . || true)
    fi
fi
if [[ ${#aur_names[@]} -gt 0 && $have_yay -eq 0 ]]; then
    echo "Update failed: yay is not installed, AUR packages cannot be updated"
    exit 1
fi
if [[ ${#flatpak_ids[@]} -gt 0 && $have_flatpak -eq 0 ]]; then
    echo "Update failed: flatpak is not installed"
    exit 1
fi

if [[ $want_repo -eq 1 ]]; then
    run_step "Update failed: " "pacman exited with an error" sudo /usr/bin/pacman -Syu --noconfirm
fi

aur_cmd=()
if [[ $selective -eq 0 && $have_yay -eq 1 ]]; then
    aur_cmd=(yay -Sua --noconfirm)
elif [[ ${#aur_names[@]} -gt 0 ]]; then
    aur_cmd=(yay -S --aur --noconfirm "${aur_names[@]}")
fi
if [[ ${#aur_cmd[@]} -gt 0 ]]; then
    run_step "Updated $repo_count packages (repo); AUR update failed: " "yay exited with an error" "${aur_cmd[@]}"
fi

if [[ $have_flatpak -eq 1 && "$flatpak_count" -gt 0 ]]; then
    flatpak_per_installation "Updated $repo_count packages (repo), $aur_count (AUR); Flatpak update failed: " \
        update -y --noninteractive -- "${flatpak_ids[@]}"
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
