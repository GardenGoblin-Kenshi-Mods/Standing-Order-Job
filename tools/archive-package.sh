#!/usr/bin/env bash
# Zip out/StandingOrderJob/ for CI artifacts and GitHub Releases.
set -euo pipefail

mod_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
package_name="StandingOrderJob"
out_dir="$mod_root/out/$package_name"

if [[ ! -f "$out_dir/$package_name.mod" || ! -f "$out_dir/meitou.json" ]]; then
    printf 'Package is incomplete. Run ./tools/package.sh first.\n' >&2
    exit 1
fi

version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$out_dir/meitou.json")
archive="$mod_root/out/${package_name}-${version}.zip"

rm -f "$archive"
(cd "$mod_root/out" && zip -qr "$archive" "$package_name")

printf '%s\n' "$archive"
