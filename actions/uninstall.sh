#!/bin/bash
# Action: uninstall packages picked in the Installed tab or offered by the unused-apps check. Never runs unattended.
# Args (at least one, repeatable; the format of actions/_lib.sh parse_args):
#   repo:<name>     Arch repo package  } pacman -Rns: with the dependencies nothing else needs, after guard_removal
#   aur:<name>      AUR package        } refused it when that set reaches a protected package, a login shell or
#                                        a package Arch Assistant needs
#   flatpak:<id>    Flatpak app (system installation via sudo, user installation as the user)
# stdout: "Removed N packages and M Flatpaks", or "Uninstall failed: <reason>".
# stderr: full pacman/flatpak output. Exit 1 on failure or a blocked removal, 2 on a bad argument (nothing runs then).
set -uo pipefail
# shellcheck source-path=SCRIPTDIR source=_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

parse_args "Uninstall" "nothing to uninstall" "$@"
pkgs=("${repo_names[@]}" "${aur_names[@]}")
if [[ ${#flatpak_ids[@]} -gt 0 ]] && ! command -v flatpak >/dev/null 2>&1; then
    echo "Uninstall failed: flatpak is not installed"
    exit 1
fi

if [[ ${#pkgs[@]} -gt 0 ]]; then
    guard_removal "Uninstall failed: " "${pkgs[@]}"
    run_step "Uninstall failed: " "pacman exited with an error" sudo /usr/bin/pacman -Rns --noconfirm "${pkgs[@]}"
fi

if [[ ${#flatpak_ids[@]} -gt 0 ]]; then
    flatpak_per_installation "Removed ${#pkgs[@]} packages; Flatpak uninstall failed: " \
        uninstall -y --noninteractive -- "${flatpak_ids[@]}"
fi

echo "Removed ${#pkgs[@]} packages and ${#flatpak_ids[@]} Flatpaks"
