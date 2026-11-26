#!/usr/bin/env bash
# Emit Markdown SHA-256 tables for the completed ZIP and its inner package files.
set -euo pipefail

mod_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
package_name="StandingOrderJob"
out_dir="$mod_root/out/$package_name"
dll="$out_dir/$package_name.dll"
mod="$out_dir/$package_name.mod"
meta="$out_dir/meitou.json"

if [[ ! -f "$dll" || ! -f "$mod" || ! -f "$meta" ]]; then
    printf 'Package binaries missing. Run ./tools/package.sh first.\n' >&2
    exit 1
fi

version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$meta")
archive_name="${package_name}-${version}.zip"
archive="$mod_root/out/$archive_name"

if [[ ! -f "$archive" ]]; then
    printf 'Release archive missing (%s). Run ./tools/archive-package.sh first.\n' "$archive" >&2
    exit 1
fi

zip_sha=$(sha256sum "$archive" | awk '{print $1}')
dll_sha=$(sha256sum "$dll" | awk '{print $1}')
mod_sha=$(sha256sum "$mod" | awk '{print $1}')
meta_sha=$(sha256sum "$meta" | awk '{print $1}')

printf '### Package checksums (SHA-256)\n\n'
printf '#### Release archive\n\n'
printf '| File | SHA-256 |\n'
printf '| --- | --- |\n'
printf '| `%s` | `%s` |\n\n' "$archive_name" "$zip_sha"
printf '#### Extracted package contents\n\n'
printf '| File | SHA-256 |\n'
printf '| --- | --- |\n'
printf '| `%s/%s.dll` | `%s` |\n' "$package_name" "$package_name" "$dll_sha"
printf '| `%s/%s.mod` | `%s` |\n' "$package_name" "$package_name" "$mod_sha"
printf '| `%s/meitou.json` | `%s` |\n' "$package_name" "$meta_sha"
