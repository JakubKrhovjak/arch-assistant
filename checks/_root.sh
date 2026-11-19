#!/bin/bash
# Shared by the checks that read root-only state; sourced, never run on its own.
#
# Checks run unattended right after login, without a tty. A plain `sudo` there fails its password conversation and
# every such failure counts towards pam_faillock, so a few checks lock the account for ten minutes (the user's
# correct password is refused meanwhile). `sudo -n` refuses at once instead of asking.
#
# The refusal has to be told apart from the command's own failure: swallowed, it reads as "nothing found" (no
# .pacnew files) or, for the Secure Boot guard, as a critical "state unknown" on every cycle. `sudo -l <command>`
# cannot be used as a probe, it succeeds without a password as soon as any NOPASSWD rule exists.

ROOT_REFUSED=77

# root <command...>: `sudo -n <command>`; returns ROOT_REFUSED and says why on stderr when sudo wants a password,
# otherwise the command's own status. LC_ALL=C because the refusal is recognised by sudo's English message.
root() {
    local err rc
    err=$(mktemp) || return
    LC_ALL=C sudo -n "$@" 2>"$err"
    rc=$?
    if (( rc != 0 )) && grep -q 'a password is required' "$err"; then
        echo "needs passwordless sudo for: $*" >&2
        rc=$ROOT_REFUSED
    else
        cat "$err" >&2
    fi
    rm -f "$err"
    return "$rc"
}

# exit_if_refused <status> [message]: ends the check with an error (logged, never notified) when root was refused.
# The message is for callers that captured root's stderr into a variable.
exit_if_refused() {
    (( $1 == ROOT_REFUSED )) || return 0
    [[ -z ${2:-} ]] || echo "$2" >&2
    exit 1
}
