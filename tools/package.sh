#!/usr/bin/env bash
# Build and package StandingOrderJob using the pinned, sandboxed Nix flake.
set -euo pipefail

mod_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
out="$mod_root/out"
package_name="StandingOrderJob"

if ! command -v nix >/dev/null 2>&1; then
    printf 'nix is required to run tools/package.sh (on Windows, use WSL2 with Nix).\n' >&2
    exit 1
fi

use_local_meitou="${USE_LOCAL_MEITOU:-0}"
if [[ "${1:-}" == "--local-meitou" ]]; then
    use_local_meitou=1
    shift
fi

nix_args=(--extra-experimental-features 'nix-command flakes' build --no-link --print-out-paths)
if [[ "$use_local_meitou" == "1" ]]; then
    : "${MEITOU_CLIENT_ROOT:?Set MEITOU_CLIENT_ROOT when using --local-meitou / USE_LOCAL_MEITOU=1.}"
    nix_args+=(--override-input meitou-client "path:$MEITOU_CLIENT_ROOT")
fi
nix_args+=(--out-link "$mod_root/out/.nix-result" "$mod_root#release-with-binlog")
nix_out=$(nix "${nix_args[@]}")
rm -f "$mod_root/out/.nix-result"

rm -rf "$out/$package_name" "$out/${package_name}-"*.zip "$out/${package_name}-"*.sha256 "$out/${package_name}.binlog"
mkdir -p "$out"
cp -R "$nix_out/$package_name" "$out/$package_name"
cp "$nix_out/${package_name}-"*.zip "$out/"
cp "$nix_out/${package_name}-"*.sha256 "$out/"
cp "$nix_out/${package_name}.binlog" "$out/"
chmod -R u+w "$out"

printf 'Built %s\n' "$out/$package_name"
