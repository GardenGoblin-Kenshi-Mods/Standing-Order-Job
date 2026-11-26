#!/usr/bin/env bash
# Interactive Nix-wrapped SteamCMD login helper for Standing Order Job.
# Keeps all writable SteamCMD session state outside the Nix store and git repo,
# verifies non-interactive re-login, and optionally exports raw config.vdf to
# GitHub Actions secrets without printing credentials.
set -euo pipefail
umask 077

mod_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
default_repo="GardenGoblin-Kenshi-Mods/Standing-Order-Job"
default_state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/standing-order-job-steamcmd"
default_secret_name="STEAM_CONFIG_VDF"
max_secret_bytes=49152

username=""
repo="$default_repo"
state_dir="$default_state_dir"
secret_name="$default_secret_name"
upload_secret=0
verify_only=0
reset_state=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Log into a dedicated Steam publishing account with a Nix-wrapped SteamCMD
outside this repository, verify cached non-interactive authentication, inspect
the generated config.vdf, and optionally upload it to GitHub Actions secrets.

Options:
  --username <name>      Dedicated Steam publishing account username (prompted if omitted)
  --repo <owner/repo>    GitHub repository for secret upload (default: $default_repo)
  --secret-name <name>   GitHub Actions secret name (default: $default_secret_name)
  --state-dir <path>     Isolated SteamCMD home dir outside git repo (default: $default_state_dir)
  --upload-secret        Upload raw config.vdf directly to GitHub secret via gh CLI
  --verify-only          Skip interactive login; run non-interactive check and inspect config.vdf
  --reset                Delete isolated state directory before logging in
  -h, --help             Show this help message

Security notes:
  - Always use a dedicated Steam publishing account, never your personal desktop Steam session.
  - Password and Steam Guard code are entered interactively; never pass a password on the CLI.
  - Raw config.vdf contents are never printed to stdout/stderr.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --username)
            [[ $# -ge 2 ]] || { printf 'ERROR: --username requires a value\n' >&2; exit 2; }
            username="$2"
            shift 2
            ;;
        --repo)
            [[ $# -ge 2 ]] || { printf 'ERROR: --repo requires a value\n' >&2; exit 2; }
            repo="$2"
            shift 2
            ;;
        --secret-name)
            [[ $# -ge 2 ]] || { printf 'ERROR: --secret-name requires a value\n' >&2; exit 2; }
            secret_name="$2"
            shift 2
            ;;
        --state-dir)
            [[ $# -ge 2 ]] || { printf 'ERROR: --state-dir requires a value\n' >&2; exit 2; }
            state_dir="$2"
            shift 2
            ;;
        --upload-secret)
            upload_secret=1
            shift
            ;;
        --verify-only)
            verify_only=1
            shift
            ;;
        --reset)
            reset_state=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'ERROR: unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if ! command -v nix >/dev/null 2>&1; then
    printf 'ERROR: nix is required to run tools/steam-login.sh (on Windows, use WSL2 with Nix).\n' >&2
    exit 1
fi

arch=$(uname -m)
if [[ "$arch" != "x86_64" ]]; then
    printf 'ERROR: nixpkgs#steamcmd requires x86_64-linux (detected %s).\n' "$arch" >&2
    exit 1
fi

# Resolve state_dir cleanly and refuse paths inside this git repository or parent Factory repo.
resolved_state_dir=$(python3 -c 'import os, sys; print(os.path.realpath(os.path.abspath(sys.argv[1])))' "$state_dir")
resolved_mod_root=$(python3 -c 'import os, sys; print(os.path.realpath(os.path.abspath(sys.argv[1])))' "$mod_root")
case "$resolved_state_dir" in
    "$resolved_mod_root"|"$resolved_mod_root"/*)
        printf 'ERROR: --state-dir must be outside the git repository (%s).\n' "$resolved_mod_root" >&2
        exit 1
        ;;
esac
if git_top=$(git -C "$mod_root" rev-parse --show-toplevel 2>/dev/null); then
    resolved_git_top=$(python3 -c 'import os, sys; print(os.path.realpath(os.path.abspath(sys.argv[1])))' "$git_top")
    case "$resolved_state_dir" in
        "$resolved_git_top"|"$resolved_git_top"/*)
            printf 'ERROR: --state-dir must be outside git checkout (%s).\n' "$resolved_git_top" >&2
            exit 1
            ;;
    esac
fi

if [[ "$reset_state" -eq 1 && -d "$resolved_state_dir" ]]; then
    printf 'Removing previous SteamCMD state directory: %s\n' "$resolved_state_dir"
    rm -rf "$resolved_state_dir"
fi

mkdir -p "$resolved_state_dir/.local/share" "$resolved_state_dir/.config"
chmod 700 "$resolved_state_dir"

if [[ -z "$username" ]]; then
    read -r -p "Dedicated Steam publishing username: " username
fi
if [[ -z "$username" ]]; then
    printf 'ERROR: Steam username cannot be empty.\n' >&2
    exit 1
fi

printf 'Resolving Nix SteamCMD and clean 32-bit FHS runtime (reusing host Nix cache)...\n'
steamcmd_pkg=$(
    NIXPKGS_ALLOW_UNFREE=1 \
    nix --extra-experimental-features 'nix-command flakes' \
        build --impure --no-link --print-out-paths 'nixpkgs#steamcmd'
)
steamcmd_share="$steamcmd_pkg/share/steamcmd"
if [[ ! -f "$steamcmd_share/steamcmd.sh" ]]; then
    printf 'ERROR: Resolved nixpkgs#steamcmd share directory is incomplete: %s\n' "$steamcmd_share" >&2
    exit 1
fi

# Build a minimal multiArch FHS env instead of nixpkgs's steam-run wrapper, which
# hardcodes `--bind /etc/zoneinfo /usr/share/zoneinfo` and fails on non-NixOS distros.
fhs_out=$(
    NIXPKGS_ALLOW_UNFREE=1 \
    nix --extra-experimental-features 'nix-command flakes' \
        build --impure --no-link --print-out-paths --expr '
let
  pkgs = import (builtins.getFlake "nixpkgs") { system = "x86_64-linux"; config.allowUnfree = true; };
in pkgs.buildFHSEnv {
  name = "standing-order-job-steamcmd-fhs";
  multiArch = true;
  targetPkgs = p: [ p.bash p.coreutils p.curl p.cacert p.tzdata ];
  multiPkgs = p: [ p.glibc p.stdenv.cc.cc.lib p.zlib ];
  runScript = "bash";
}
'
)
fhs_bin="$fhs_out/bin/standing-order-job-steamcmd-fhs"
if [[ ! -x "$fhs_bin" ]]; then
    printf 'ERROR: Clean SteamCMD FHS runtime is missing or not executable: %s\n' "$fhs_bin" >&2
    exit 1
fi

steam_root="$resolved_state_dir/.local/share/Steam"
if [[ ! -e "$steam_root" ]]; then
    mkdir -p "$steam_root"/{appcache,config,logs,Steamapps/common}
    mkdir -p "$resolved_state_dir/.steam"
    ln -sfn "$steam_root" "$resolved_state_dir/.steam/root"
    ln -sfn "$steam_root" "$resolved_state_dir/.steam/steam"
fi
if [[ ! -x "$steam_root/steamcmd.sh" ]]; then
    mkdir -p "$steam_root/linux32"
    (
        cd "$steamcmd_share"
        find . -type f -exec install -Dm 755 "{}" "$steam_root/{}" \;
    )
fi

if [[ "$verify_only" -eq 0 ]]; then
    printf '\n==> Step 1/3: Interactive SteamCMD login (enter password and Steam Guard when prompted)\n'
    printf '    Isolated HOME: %s\n' "$resolved_state_dir"
    HOME="$resolved_state_dir" \
    XDG_DATA_HOME="$resolved_state_dir/.local/share" \
    XDG_CONFIG_HOME="$resolved_state_dir/.config" \
        "$fhs_bin" -c 'exec "$1" "${@:2}"' _ "$steam_root/steamcmd.sh" +login "$username" +quit
fi

printf '\n==> Step 2/3: Non-interactive cached session verification (no password / no Steam Guard prompt)\n'
tmp_log=$(mktemp "${TMPDIR:-/tmp}/steam-login-verify.XXXXXX")
chmod 600 "$tmp_log"
trap 'rm -f "$tmp_log"' EXIT

set +e
timeout 60s env \
    HOME="$resolved_state_dir" \
    XDG_DATA_HOME="$resolved_state_dir/.local/share" \
    XDG_CONFIG_HOME="$resolved_state_dir/.config" \
    "$fhs_bin" -c 'exec "$1" "${@:2}"' _ \
        "$steam_root/steamcmd.sh" \
        +@ShutdownOnFailedCommand 1 \
        +@NoPromptForPassword 1 \
        +login "$username" \
        +quit </dev/null >"$tmp_log" 2>&1
verify_rc=$?
set -e

python3 "$mod_root/.github/scripts/steam_ci_helpers.py" check-auth-log \
    --log-file "$tmp_log" \
    --exit-code "$verify_rc" \
    --username "$username"

printf '\n==> Step 3/3: Inspecting generated SteamCMD session files (paths and sizes only)\n'
mapfile -t found_configs < <(find "$resolved_state_dir" -type f -name 'config.vdf' | sort)
if [[ ${#found_configs[@]} -eq 0 ]]; then
    printf 'ERROR: No config.vdf found under %s.\n' "$resolved_state_dir" >&2
    exit 1
fi

config_vdf=""
for candidate in "${found_configs[@]}"; do
    chmod 600 "$candidate"
    rel_path="${candidate#"$resolved_state_dir"/}"
    byte_size=$(wc -c < "$candidate")
    has_cache=$(python3 "$mod_root/.github/scripts/steam_ci_helpers.py" inspect-config --file "$candidate" --max-bytes "$max_secret_bytes")
    printf '  - %s (%s bytes; %s)\n' "$rel_path" "$byte_size" "$has_cache"
    if [[ -z "$config_vdf" && "$has_cache" == *"ConnectCache=present"* ]]; then
        config_vdf="$candidate"
    fi
done

if [[ -z "$config_vdf" ]]; then
    printf 'ERROR: Found config.vdf file(s), but none contained a ConnectCache session token.\n' >&2
    exit 1
fi

printf '\nSession-related files observed under %s (for Milestone 1 feasibility check):\n' "$resolved_state_dir"
find "$resolved_state_dir" -maxdepth 4 \( -name 'config.vdf' -o -name 'loginusers.vdf' -o -name 'ssfn*' \) -type f | sort | while IFS= read -r item; do
    rel_item="${item#"$resolved_state_dir"/}"
    sz=$(wc -c < "$item")
    printf '  - %s (%s bytes)\n' "$rel_item" "$sz"
done

selected_rel="${config_vdf#"$resolved_state_dir"/}"
selected_bytes=$(wc -c < "$config_vdf")
printf '\nSelected config.vdf: %s (%s bytes, mode 600)\n' "$config_vdf" "$selected_bytes"

if [[ "$selected_bytes" -gt "$max_secret_bytes" ]]; then
    printf 'ERROR: config.vdf (%s bytes) exceeds GitHub Actions secret size limit (%s bytes).\n' \
        "$selected_bytes" "$max_secret_bytes" >&2
    exit 1
fi

resolve_gh_cmd() {
    if command -v gh >/dev/null 2>&1; then
        printf 'gh\n'
        return 0
    fi
    local factory_gh="$mod_root/../../tools/gh.sh"
    if [[ -x "$factory_gh" ]]; then
        printf '%s\n' "$factory_gh"
        return 0
    fi
    return 1
}

if [[ "$upload_secret" -eq 1 ]]; then
    gh_bin=$(resolve_gh_cmd) || {
        printf 'ERROR: gh CLI not found on PATH and ../../tools/gh.sh unavailable.\n' >&2
        exit 1
    }
    printf '\nUploading raw %s to GitHub Actions secret %s in %s...\n' "$selected_rel" "$secret_name" "$repo"
    "$gh_bin" secret set "$secret_name" --repo "$repo" < "$config_vdf"
    printf 'Uploaded %s successfully (contents not printed).\n' "$secret_name"
else
    printf '\nNext steps to configure GitHub Actions (raw text, no Base64 needed):\n'
    printf '  1. Upload %s secret without printing contents:\n' "$secret_name"
    printf '       gh secret set %s --repo %s < %q\n' "$secret_name" "$repo" "$config_vdf"
    printf '     (or re-run this script with --verify-only --upload-secret)\n'
    printf '  2. Set STEAM_USERNAME secret (if not already set):\n'
    printf '       gh secret set STEAM_USERNAME --repo %s\n' "$repo"
    printf '  3. Run Gate 1B twice on two fresh hosted runners:\n'
    printf '       gh workflow run validate-steam-session.yml --repo %s\n' "$repo"
fi
