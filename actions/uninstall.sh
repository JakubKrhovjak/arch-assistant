#!/bin/bash
# Action: uninstall packages picked in the Installed tab. Never runs unattended.
# Args (at least one, repeatable):
#   pkg:<name>      Arch repo or AUR package (pacman -Rns: with its no longer needed dependencies)
#   flatpak:<id>    Flatpak app (system installation via sudo, user installation as the user)
# stdout: "Removed N packages and M Flatpaks", or "Uninstall failed: <reason>".
# stderr: full pacman/flatpak output. Exit 1 on failure, 2 on a bad argument (nothing runs then).
set -uo pipefail

pkgs=()
flatpaks=()
if [[ $# -eq 0 ]]; then
    echo "Uninstall failed: nothing to uninstall"
    exit 2
fi
for arg in "$@"; do
    name="${arg#*:}"
    # package names and Flatpak ids only: nothing that pacman or flatpak could read as an option
    if [[ "$arg" != *:* || ! "$name" =~ ^[A-Za-z0-9@_+][A-Za-z0-9@._+-]*$ ]]; then
        echo "Uninstall failed: invalid argument $arg"
        exit 2
    fi
    case "$arg" in
        pkg:*) pkgs+=("$name") ;;
        flatpak:*) flatpaks+=("$name") ;;
        *) echo "Uninstall failed: unknown argument $arg"; exit 2 ;;
    esac
done

err_file=$(mktemp)
trap 'rm -f "$err_file"' EXIT

fail() {
    cat "$err_file" >&2
    reason=$(grep -m1 -i "error" "$err_file" || head -n1 "$err_file")
    echo "$1${reason:-$2}"
    exit 1
}

if [[ ${#pkgs[@]} -gt 0 ]]; then
    if ! sudo /usr/bin/pacman -Rns --noconfirm "${pkgs[@]}" 2>"$err_file" 1>&2; then
        fail "Uninstall failed: " "pacman exited with an error"
    fi
    cat "$err_file" >&2
fi

if [[ ${#flatpaks[@]} -gt 0 ]]; then
    if ! command -v flatpak >/dev/null 2>&1; then
        echo "Uninstall failed: flatpak is not installed"
        exit 1
    fi
    user_apps=$(flatpak list --app --user --columns=application 2>/dev/null || true)
    system_ids=()
    user_ids=()
    for id in "${flatpaks[@]}"; do
        if grep -qxF "$id" <<<"$user_apps"; then user_ids+=("$id"); else system_ids+=("$id"); fi
    done
    done_pkgs="Removed ${#pkgs[@]} packages; "
    if [[ ${#system_ids[@]} -gt 0 ]]; then
        # system-wide Flatpaks: sudo instead of a polkit prompt that cannot be answered in the background
        if ! sudo /usr/bin/flatpak uninstall --system -y --noninteractive "${system_ids[@]}" 1>&2 2>"$err_file"; then
            fail "${done_pkgs}Flatpak uninstall failed: " "flatpak exited with an error"
        fi
    fi
    if [[ ${#user_ids[@]} -gt 0 ]]; then
        if ! flatpak uninstall --user -y --noninteractive "${user_ids[@]}" 1>&2 2>"$err_file"; then
            fail "${done_pkgs}Flatpak uninstall failed: " "flatpak exited with an error"
        fi
    fi
fi

echo "Removed ${#pkgs[@]} packages and ${#flatpaks[@]} Flatpaks"
