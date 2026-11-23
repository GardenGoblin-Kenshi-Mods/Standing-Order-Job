#!/usr/bin/env bash
# Emit a Markdown SHA-256 section for the packaged .dll and .mod (for release notes).
set -euo pipefail

mod_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
package_name="StandingOrderJob"
out_dir="$mod_root/out/$package_name"
dll="$out_dir/$package_name.dll"
mod="$out_dir/$package_name.mod"

if [[ ! -f "$dll" || ! -f "$mod" ]]; then
    printf 'Package binaries missing. Run ./tools/package.sh first.\n' >&2
    exit 1
fi

dll_sha=$(sha256sum "$dll" | awk '{print $1}')
mod_sha=$(sha256sum "$mod" | awk '{print $1}')

printf '### Package checksums (SHA-256)\n\n'
printf '| File | SHA-256 |\n'
printf '| --- | --- |\n'
printf '| `%s` | `%s` |\n' "$package_name.dll" "$dll_sha"
printf '| `%s` | `%s` |\n' "$package_name.mod" "$mod_sha"
