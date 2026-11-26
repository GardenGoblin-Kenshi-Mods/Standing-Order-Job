#!/usr/bin/env bash
# Repository-owned SteamCMD wrapper for GitHub Actions (no third-party publishing action).
# Never enables shell xtrace around credentialed commands and never prints config.vdf contents.
set -euo pipefail
umask 077

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
helper_py="$script_dir/steam_ci_helpers.py"
work_root="${STEAM_CI_WORK_ROOT:-${RUNNER_TEMP:-/tmp}/standing-order-job-steam}"
steamcmd_dir="$work_root/steamcmd"
steam_home="$work_root/steam-home"
staging_dir="$work_root/workshop-staging"

require_steamcmd() {
    if [[ ! -x "$steamcmd_dir/steamcmd.sh" ]]; then
        printf '::error::SteamCMD is not installed at %s/steamcmd.sh. Run install first.\n' "$steamcmd_dir" >&2
        exit 1
    fi
}

cmd_install() {
    mkdir -p "$steamcmd_dir" "$steam_home/.local/share" "$steam_home/.config"
    chmod 700 "$work_root" "$steam_home"

    printf 'Installing 32-bit SteamCMD runtime libraries on Ubuntu...\n'
    sudo dpkg --add-architecture i386
    sudo apt-get update -qq
    sudo apt-get install -y -qq lib32gcc-s1 lib32stdc++6 ca-certificates curl tar

    printf 'Downloading Valve steamcmd_linux.tar.gz into %s...\n' "$steamcmd_dir"
    curl -fsSL "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz" \
        | tar -xzf - -C "$steamcmd_dir"

    printf 'Bootstrapping SteamCMD self-update (anonymous +quit)...\n'
    local boot_log
    boot_log=$(mktemp "$work_root/steamcmd-bootstrap.XXXXXX")
    chmod 600 "$boot_log"
    set +e
    HOME="$steam_home" \
    XDG_DATA_HOME="$steam_home/.local/share" \
    XDG_CONFIG_HOME="$steam_home/.config" \
    timeout 180s "$steamcmd_dir/steamcmd.sh" +quit </dev/null >"$boot_log" 2>&1
    local boot_rc=$?
    set -e
    rm -f "$boot_log"
    if [[ "$boot_rc" -ne 0 ]]; then
        printf '::error::SteamCMD initial bootstrap failed (exit=%s).\n' "$boot_rc" >&2
        exit 1
    fi

    printf 'SteamCMD config directories present after bootstrap:\n'
    find "$steam_home" "$steamcmd_dir" -maxdepth 4 -type d -name config 2>/dev/null | sort | sed 's/^/  - /' || true
}

cmd_restore_session() {
    mkdir -p "$steam_home/.local/share" "$steam_home/.config"
    chmod 700 "$work_root" "$steam_home"

    python3 "$helper_py" restore-config \
        --steam-home "$steam_home" \
        --steamcmd-dir "$steamcmd_dir"
}

cmd_auth_check() {
    require_steamcmd
    if [[ -z "${STEAM_USERNAME:-}" ]]; then
        printf '::error::STEAM_USERNAME environment variable is empty.\n' >&2
        exit 1
    fi

    local auth_log
    auth_log=$(mktemp "$work_root/steam-auth.XXXXXX")
    chmod 600 "$auth_log"

    printf 'Running non-interactive SteamCMD authentication check (timeout=90s)...\n'
    set +e
    HOME="$steam_home" \
    XDG_DATA_HOME="$steam_home/.local/share" \
    XDG_CONFIG_HOME="$steam_home/.config" \
    timeout 90s "$steamcmd_dir/steamcmd.sh" \
        +@ShutdownOnFailedCommand 1 \
        +@NoPromptForPassword 1 \
        +login "$STEAM_USERNAME" \
        +quit </dev/null >"$auth_log" 2>&1
    local auth_rc=$?
    python3 "$helper_py" check-auth-log \
        --log-file "$auth_log" \
        --exit-code "$auth_rc" \
        --username "$STEAM_USERNAME" \
        --github-output "${GITHUB_OUTPUT:-}"
    local check_rc=$?
    set -e
    rm -f "$auth_log"
    return "$check_rc"
}

cmd_upload() {
    local vdf_path="${1:-}"
    if [[ -z "$vdf_path" || ! -f "$vdf_path" ]]; then
        printf '::error::Workshop VDF path is missing or does not exist: %s\n' "$vdf_path" >&2
        exit 1
    fi
    require_steamcmd
    if [[ -z "${STEAM_USERNAME:-}" ]]; then
        printf '::error::STEAM_USERNAME environment variable is empty.\n' >&2
        exit 1
    fi

    local upload_log
    upload_log=$(mktemp "$work_root/steam-upload.XXXXXX")
    chmod 600 "$upload_log"

    printf 'Running non-interactive SteamCMD workshop_build_item (vdf=%s, timeout=300s)...\n' "$vdf_path"
    set +e
    HOME="$steam_home" \
    XDG_DATA_HOME="$steam_home/.local/share" \
    XDG_CONFIG_HOME="$steam_home/.config" \
    timeout 300s "$steamcmd_dir/steamcmd.sh" \
        +@ShutdownOnFailedCommand 1 \
        +@NoPromptForPassword 1 \
        +login "$STEAM_USERNAME" \
        +workshop_build_item "$vdf_path" \
        +quit </dev/null >"$upload_log" 2>&1
    local upload_rc=$?
    python3 "$helper_py" check-upload-log \
        --log-file "$upload_log" \
        --exit-code "$upload_rc" \
        --username "$STEAM_USERNAME" \
        --github-output "${GITHUB_OUTPUT:-}"
    local check_rc=$?
    set -e
    rm -f "$upload_log"
    return "$check_rc"
}

cmd_cleanup() {
    rm -rf "$steam_home" "$staging_dir" "$work_root"/steam-*.XXXXXX 2>/dev/null || true
    find "$work_root" -maxdepth 1 -type f -name 'steam-*.??????' -delete 2>/dev/null || true
    printf 'Cleaned up ephemeral SteamCMD session and staging directories.\n'
}

subcommand="${1:-}"
shift || true
case "$subcommand" in
    install) cmd_install "$@" ;;
    restore-session) cmd_restore_session "$@" ;;
    auth-check) cmd_auth_check "$@" ;;
    upload) cmd_upload "$@" ;;
    cleanup) cmd_cleanup "$@" ;;
    *)
        printf 'Usage: %s <install|restore-session|auth-check|upload <vdf>|cleanup>\n' "$(basename "$0")" >&2
        exit 2
        ;;
esac
