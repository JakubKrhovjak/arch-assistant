#!/bin/bash
# Action: system update (repo via pacman, then AUR via yay, then Flatpak apps and runtimes).
# Args: none = update everything. Otherwise any of
#   repo            all repo packages (pacman -Syu; partial repo upgrades are unsupported on Arch)
#   aur:<name>      this AUR package (repeatable)
#   flatpak:<id>    this Flatpak (repeatable)
# stdout: "Updated N packages (repo), M (AUR) and K Flatpaks" [+ "New .pacnew files: K"], or "Update failed: <reason>".
# stderr: full pacman/yay output. Exit 1 if pacman fails (yay is skipped then), 2 on a bad argument.
# Env: AA_PACMAN_LOG (default /var/log/pacman.log), AA_UPDATE_SINCE (ISO timestamp, default now).
set -uo pipefail

pacman_log="${AA_PACMAN_LOG:-/var/log/pacman.log}"
since="${AA_UPDATE_SINCE:-$(date +%Y-%m-%dT%H:%M:%S)}"

selective=0
want_repo=1
aur_names=()
flatpak_ids=()
if [[ $# -gt 0 ]]; then
    selective=1
    want_repo=0
    for arg in "$@"; do
        name="${arg#*:}"
        # package names and Flatpak ids only: nothing that pacman, yay or flatpak could read as an option
        if [[ "$arg" == *:* && ! "$name" =~ ^[A-Za-z0-9@_+][A-Za-z0-9@._+-]*$ ]]; then
            echo "Update failed: invalid package name in argument $arg"
            exit 2
        fi
        case "$arg" in
            repo) want_repo=1 ;;
            aur:*) aur_names+=("$name") ;;
            flatpak:*) flatpak_ids+=("$name") ;;
            *) echo "Update failed: unknown argument $arg"; exit 2 ;;
        esac
    done
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

err_file=$(mktemp)
trap 'rm -f "$err_file"' EXIT

if [[ $want_repo -eq 1 ]]; then
    if ! sudo /usr/bin/pacman -Syu --noconfirm 2>"$err_file" 1>&2; then
        cat "$err_file" >&2
        reason=$(grep -m1 -i "error" "$err_file" || head -n1 "$err_file")
        echo "Update failed: ${reason:-pacman exited with an error}"
        exit 1
    fi
    cat "$err_file" >&2
fi

aur_cmd=()
if [[ $selective -eq 0 && $have_yay -eq 1 ]]; then
    aur_cmd=(yay -Sua --noconfirm)
elif [[ ${#aur_names[@]} -gt 0 ]]; then
    aur_cmd=(yay -S --aur --noconfirm "${aur_names[@]}")
fi
if [[ ${#aur_cmd[@]} -gt 0 ]]; then
    if ! "${aur_cmd[@]}" 1>&2 2>"$err_file"; then
        cat "$err_file" >&2
        reason=$(grep -m1 -i "error" "$err_file" || head -n1 "$err_file")
        echo "Updated $repo_count packages (repo); AUR update failed: ${reason:-yay exited with an error}"
        exit 1
    fi
fi

if [[ $have_flatpak -eq 1 && "$flatpak_count" -gt 0 ]]; then
    # system-wide Flatpaks: sudo instead of a polkit prompt that cannot be answered in the background
    if ! sudo /usr/bin/flatpak update -y --noninteractive "${flatpak_ids[@]}" 1>&2 2>"$err_file"; then
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
