#!/bin/bash
# Action: install packages picked in the Browse tab. Never runs unattended.
# Args (at least one, repeatable; the format of actions/_lib.sh parse_args):
#   repo:<name>     Arch repo package (pacman -S --needed)
#   aur:<name>      AUR package (yay -S --aur --needed, built from its PKGBUILD)
#   flatpak:<id>    Flatpak app from Flathub (system installation via sudo)
# stdout: "Installed N packages (repo), M (AUR) and K Flatpaks", or "Install failed: <reason>".
# stderr: full pacman/yay/flatpak output. Exit 1 on failure, 2 on a bad argument (nothing runs then).
set -uo pipefail
# shellcheck source-path=SCRIPTDIR source=_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

parse_args "Install" "nothing to install" "$@"
if [[ ${#aur_names[@]} -gt 0 ]] && ! command -v yay >/dev/null 2>&1; then
    echo "Install failed: yay is not installed, AUR packages cannot be installed"
    exit 1
fi
if [[ ${#flatpak_ids[@]} -gt 0 ]] && ! command -v flatpak >/dev/null 2>&1; then
    echo "Install failed: flatpak is not installed"
    exit 1
fi

if [[ ${#repo_names[@]} -gt 0 ]]; then
    run_step "Install failed: " "pacman exited with an error" \
        sudo /usr/bin/pacman -S --needed --noconfirm "${repo_names[@]}"
fi

if [[ ${#aur_names[@]} -gt 0 ]]; then
    # --noconfirm skips the PKGBUILD review: a conscious choice the Browse tab states (AUR_NOTE, INSTALL_NOTE)
    run_step "Installed ${#repo_names[@]} packages (repo); AUR install failed: " "yay exited with an error" \
        yay -S --aur --needed --noconfirm "${aur_names[@]}"
fi

if [[ ${#flatpak_ids[@]} -gt 0 ]]; then
    # new Flatpaks always go to the system installation, from Flathub
    run_step "Installed ${#repo_names[@]} packages (repo), ${#aur_names[@]} (AUR); Flatpak install failed: " \
        "flatpak exited with an error" \
        sudo /usr/bin/flatpak install --system -y --noninteractive flathub "${flatpak_ids[@]}"
fi

echo "Installed ${#repo_names[@]} packages (repo), ${#aur_names[@]} (AUR) and ${#flatpak_ids[@]} Flatpaks"
