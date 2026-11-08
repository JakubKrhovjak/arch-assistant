#!/bin/bash
# Shared part of the package actions (update.sh, install.sh, uninstall.sh); sourced, never run on its own.
# Provides: parse_args (the one argument format), run_step (run, capture stderr, report the first error line),
# flatpak_per_installation (system Flatpaks via sudo, user ones as the user) and guard_removal (the uninstall guard).
# Sourcing sets up $err_file (removed on exit).

lib_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
assets_dir="$lib_dir/../assets"
# shellcheck source-path=SCRIPTDIR source=_lists.sh
source "$lib_dir/_lists.sh"   # list_file
err_file=$(mktemp)
trap 'rm -f "$err_file"' EXIT

repo_names=()
aur_names=()
flatpak_ids=()

# parse_args <label> <message when there are no args, "" = none needed> <args...>
# Arguments are "<source>:<name>" with source repo, aur or flatpak (arch_assistant/packages.py package_arg builds
# them). Fills repo_names, aur_names and flatpak_ids; anything else prints "<label> failed: …" and exits 2 before
# anything runs. Names only: nothing that pacman, yay or flatpak could read as an option.
parse_args() {
    local label=$1 empty=$2 arg name
    shift 2
    if [[ $# -eq 0 && -n $empty ]]; then
        echo "$label failed: $empty"
        exit 2
    fi
    for arg in "$@"; do
        name="${arg#*:}"
        if [[ "$arg" != *:* || ! "$name" =~ ^[A-Za-z0-9@_+][A-Za-z0-9@._+-]*$ ]]; then
            echo "$label failed: invalid argument $arg"
            exit 2
        fi
        case "$arg" in
            repo:*) repo_names+=("$name") ;;
            aur:*) aur_names+=("$name") ;;
            flatpak:*) flatpak_ids+=("$name") ;;
            *) echo "$label failed: unknown argument $arg"; exit 2 ;;
        esac
    done
}

# report_failure <stdout prefix> <reason when stderr has none>: the captured stderr goes to stderr, the first
# error line (or the first line) to stdout after the prefix, then exit 1.
report_failure() {
    local reason
    cat "$err_file" >&2
    reason=$(grep -m1 -i "error" "$err_file" || head -n1 "$err_file")
    echo "$1${reason:-$2}"
    exit 1
}

# run_step <stdout prefix on failure> <reason when stderr has none> <command...>
# The command's stdout and stderr both end up on stderr (shown live / logged); a failure is reported and exits 1.
run_step() {
    local prefix=$1 fallback=$2
    shift 2
    if ! "$@" 2>"$err_file" 1>&2; then
        report_failure "$prefix" "$fallback"
    fi
    cat "$err_file" >&2
}

# flatpak_per_installation <stdout prefix on failure> <flatpak subcommand> <options...> -- [ids...]
# Runs the subcommand per installation: the system one through sudo (a polkit prompt cannot be answered from a
# background job), the user one as the user. Without ids it covers the whole installation (a full update); the
# user installation is then left out when it has nothing installed.
flatpak_per_installation() {
    local prefix=$1 sub=$2 opts=() user_refs id system_ids=() user_ids=()
    shift 2
    while [[ $# -gt 0 && $1 != -- ]]; do opts+=("$1"); shift; done
    [[ $# -gt 0 ]] && shift
    user_refs=$(flatpak list --user --columns=application 2>/dev/null || true)
    for id in "$@"; do
        if grep -qxF "$id" <<<"$user_refs"; then user_ids+=("$id"); else system_ids+=("$id"); fi
    done
    if [[ $# -eq 0 || ${#system_ids[@]} -gt 0 ]]; then
        run_step "$prefix" "flatpak exited with an error" \
            sudo /usr/bin/flatpak "$sub" --system "${opts[@]}" "${system_ids[@]}"
    fi
    if [[ ${#user_ids[@]} -gt 0 || ( $# -eq 0 && -n $user_refs ) ]]; then
        run_step "$prefix" "flatpak exited with an error" flatpak "$sub" --user "${opts[@]}" "${user_ids[@]}"
    fi
}

# guard_removal <stdout prefix on failure> <packages...>
# The uninstall guard, a mirror of arch_assistant/protection.py (same lists, same reasons): asks pacman for every
# package `pacman -Rns` would remove (read-only, no root) and exits 1 when that set reaches a protected package
# (assets/protected.txt), the login shell of this user or root, or a package Arch Assistant needs
# (assets/dependencies.txt).
guard_removal() {
    local prefix=$1 targets entry pkg pattern reason patterns=() groups=() members shells deps blocked=()
    shift
    if ! targets=$(LC_ALL=C.UTF-8 pacman -Rs --print --print-format '%n' "$@" 2>"$err_file"); then
        report_failure "$prefix" "pacman cannot resolve the removal"
    fi
    while read -r entry; do
        if [[ $entry == @* ]]; then groups+=("${entry#@}"); else patterns+=("$entry"); fi
    done < <(list_file "$assets_dir/protected.txt")
    members=$(pacman -Qgq "${groups[@]}" 2>/dev/null || true)
    shells=$(getent passwd "$(id -un)" root | cut -d: -f7 | xargs -r pacman -Qoq 2>/dev/null || true)
    deps=$(list_file "$assets_dir/dependencies.txt")
    while read -r pkg; do
        [[ -z $pkg ]] && continue
        reason=""
        for pattern in "${patterns[@]}"; do
            # shellcheck disable=SC2053  # the right side is an fnmatch pattern on purpose
            [[ $pkg == $pattern ]] && reason="protected system package" && break
        done
        if [[ -z $reason ]] && grep -qxF "$pkg" <<<"$members"; then
            reason="protected system package"
        elif [[ -z $reason ]] && grep -qxF "$pkg" <<<"$shells"; then
            reason="login shell"
        elif [[ -z $reason ]] && grep -qxF "$pkg" <<<"$deps"; then
            reason="needed by Arch Assistant"
        fi
        [[ -n $reason ]] && blocked+=("$pkg ($reason)")
    done <<<"$targets"
    if [[ ${#blocked[@]} -gt 0 ]]; then
        local joined
        joined=$(printf '%s, ' "${blocked[@]}")
        echo "${prefix}blocked: ${joined%, }"
        exit 1
    fi
}
