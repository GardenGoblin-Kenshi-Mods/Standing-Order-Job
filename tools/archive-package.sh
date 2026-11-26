#!/usr/bin/env bash
# Deterministically zip out/StandingOrderJob/ and emit non-circular SHA-256 manifests.
set -euo pipefail

mod_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
package_name="StandingOrderJob"
out_dir="$mod_root/out/$package_name"

if [[ ! -f "$out_dir/$package_name.mod" || ! -f "$out_dir/meitou.json" || ! -f "$out_dir/$package_name.dll" ]]; then
    printf 'Package is incomplete. Run ./tools/package.sh first.\n' >&2
    exit 1
fi

version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$out_dir/meitou.json")
archive_name="${package_name}-${version}.zip"
archive="$mod_root/out/$archive_name"
contents_sha="$mod_root/out/${package_name}-${version}.contents.sha256"
zip_sha="$mod_root/out/${archive_name}.sha256"

rm -f "$archive" "$contents_sha" "$zip_sha"

python3 - "$mod_root/out" "$package_name" "$archive" "$contents_sha" <<'PYEOF'
import hashlib
import os
import sys
import zipfile
from pathlib import Path

out_root = Path(sys.argv[1])
package_name = sys.argv[2]
archive_path = Path(sys.argv[3])
contents_sha_path = Path(sys.argv[4])

package_dir = out_root / package_name
files = sorted(
    (p for p in package_dir.rglob("*") if p.is_file()),
    key=lambda p: p.relative_to(out_root).as_posix(),
)

sha_lines: list[str] = []
with zipfile.ZipFile(archive_path, "w") as zf:
    for file_path in files:
        rel_posix = file_path.relative_to(out_root).as_posix()
        data = file_path.read_bytes()
        sha_lines.append(f"{hashlib.sha256(data).hexdigest()}  {rel_posix}\n")

        info = zipfile.ZipInfo(filename=rel_posix, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = (0o644 & 0xFFFF) << 16
        info.extra = b""
        zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

contents_sha_path.write_text("".join(sha_lines), encoding="utf-8")
PYEOF

(cd "$mod_root/out" && sha256sum "$archive_name" > "${archive_name}.sha256")

printf '%s\n' "$archive"
