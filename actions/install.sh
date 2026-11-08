#!/bin/bash
# Action: install packages picked in the Browse tab. Never runs unattended.
# Args (at least one, repeatable):
#   repo:<name>     Arch repo package (pacman -S --needed)
#   aur:<name>      AUR package (yay -S --aur --needed, built from its PKGBUILD)
#   flatpak:<id>    Flatpak app from Flathub (system installation via sudo)
# stdout: "Installed N packages (repo), M (AUR) and K Flatpaks", or "Install failed: <reason>".
# stderr: full pacman/yay/flatpak output. Exit 1 on failure, 2 on a bad argument (nothing runs then).
set -uo pipefail

repo_names=()
aur_names=()
flatpak_ids=()
if [[ $# -eq 0 ]]; then
    echo "Install failed: nothing to install"
    exit 2
fi
for arg in "$@"; do
    name="${arg#*:}"
    # package names and Flatpak ids only: nothing that pacman, yay or flatpak could read as an option
    if [[ "$arg" != *:* || ! "$name" =~ ^[A-Za-z0-9@_+][A-Za-z0-9@._+-]*$ ]]; then
        echo "Install failed: invalid argument $arg"
        exit 2
    fi
    case "$arg" in
        repo:*) repo_names+=("$name") ;;
        aur:*) aur_names+=("$name") ;;
        flatpak:*) flatpak_ids+=("$name") ;;
        *) echo "Install failed: unknown argument $arg"; exit 2 ;;
    esac
done
if [[ ${#aur_names[@]} -gt 0 ]] && ! command -v yay >/dev/null 2>&1; then
    echo "Install failed: yay is not installed, AUR packages cannot be installed"
    exit 1
fi
if [[ ${#flatpak_ids[@]} -gt 0 ]] && ! command -v flatpak >/dev/null 2>&1; then
    echo "Install failed: flatpak is not installed"
    exit 1
fi

err_file=$(mktemp)
trap 'rm -f "$err_file"' EXIT

fail() {
    cat "$err_file" >&2
    reason=$(grep -m1 -i "error" "$err_file" || head -n1 "$err_file")
    echo "$1${reason:-$2}"
    exit 1
}

if [[ ${#repo_names[@]} -gt 0 ]]; then
    if ! sudo /usr/bin/pacman -S --needed --noconfirm "${repo_names[@]}" 2>"$err_file" 1>&2; then
        fail "Install failed: " "pacman exited with an error"
    fi
    cat "$err_file" >&2
fi

if [[ ${#aur_names[@]} -gt 0 ]]; then
    if ! yay -S --aur --needed --noconfirm "${aur_names[@]}" 1>&2 2>"$err_file"; then
        fail "Installed ${#repo_names[@]} packages (repo); AUR install failed: " "yay exited with an error"
    fi
fi

if [[ ${#flatpak_ids[@]} -gt 0 ]]; then
    # system-wide like update.sh: sudo instead of a polkit prompt that cannot be answered in the background
    if ! sudo /usr/bin/flatpak install --system -y --noninteractive flathub "${flatpak_ids[@]}" 1>&2 2>"$err_file"; then
        fail "Installed ${#repo_names[@]} packages (repo), ${#aur_names[@]} (AUR); Flatpak install failed: " \
            "flatpak exited with an error"
    fi
fi

echo "Installed ${#repo_names[@]} packages (repo), ${#aur_names[@]} (AUR) and ${#flatpak_ids[@]} Flatpaks"
