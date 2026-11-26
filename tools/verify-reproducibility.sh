#!/usr/bin/env bash
# Verify byte-for-byte Nix build reproducibility, ZIP contents parity, and embedded PDB/Source Link metadata.
set -euo pipefail

if ! command -v nix >/dev/null 2>&1; then
    printf 'nix is required to run tools/verify-reproducibility.sh\n' >&2
    exit 1
fi

mod_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
version=$(tr -d '[:space:]' < "$mod_root/version.txt")
nix_cmd=(nix --extra-experimental-features 'nix-command flakes')

printf '==> Running first sandboxed Nix build (deterministic release output)\n'
out1=$("${nix_cmd[@]}" build --no-link --print-out-paths "$mod_root#release")

printf '==> Rebuilding derivation in Nix sandbox (--rebuild)\n'
"${nix_cmd[@]}" build --rebuild --no-link "$mod_root#release"

printf '==> Verifying archive and DLL byte-for-byte identity\n'
zip_path="$out1/StandingOrderJob-${version}.zip"
dll_path="$out1/StandingOrderJob/StandingOrderJob.dll"
mod_path="$out1/StandingOrderJob/StandingOrderJob.mod"

(cd "$out1" && sha256sum -c "StandingOrderJob-${version}.zip.sha256")
(cd "$out1" && sha256sum -c "StandingOrderJob-${version}.contents.sha256")

python3 - "$zip_path" "$dll_path" "$mod_path" "$mod_root/out" "$version" <<'PYEOF'
import hashlib
import sys
import zipfile
from pathlib import Path

zip_path = Path(sys.argv[1])
dll_path = Path(sys.argv[2])
mod_path = Path(sys.argv[3])
local_out = Path(sys.argv[4])
version = sys.argv[5]

with zipfile.ZipFile(zip_path, "r") as zf:
    zip_dll = zf.read("StandingOrderJob/StandingOrderJob.dll")
    zip_mod = zf.read("StandingOrderJob/StandingOrderJob.mod")

assert zip_dll == dll_path.read_bytes(), "DLL inside ZIP differs from standalone DLL!"
assert zip_mod == mod_path.read_bytes(), ".mod inside ZIP differs from standalone .mod!"

local_zip = local_out / f"StandingOrderJob-{version}.zip"
local_dll = local_out / "StandingOrderJob" / "StandingOrderJob.dll"
if local_zip.is_file() and local_dll.is_file():
    assert local_zip.read_bytes() == zip_path.read_bytes(), "out/ ZIP differs from deterministic Nix release!"
    assert local_dll.read_bytes() == dll_path.read_bytes(), "out/ DLL differs from deterministic Nix release!"

print(f"ZIP <-> standalone DLL/mod byte parity verified (DLL sha256={hashlib.sha256(zip_dll).hexdigest()})")
PYEOF

if [[ -f "$mod_root/out/StandingOrderJob.binlog" ]]; then
    bash "$mod_root/tools/check-binlog-secrets.sh" "$mod_root/out/StandingOrderJob.binlog"
fi

printf '==> Inspecting embedded portable PDB, Source Link, and deterministic paths\n'
python3 - "$dll_path" <<'PYEOF'
import struct
import sys
import zlib
from pathlib import Path

dll_bytes = Path(sys.argv[1]).read_bytes()
mpdb_idx = dll_bytes.find(b"MPDB")
assert mpdb_idx != -1, "Embedded Portable PDB (MPDB) signature not found in DLL!"

uncomp_size = struct.unpack_from("<I", dll_bytes, mpdb_idx + 4)[0]
pdb_bytes = zlib.decompress(dll_bytes[mpdb_idx + 8:], -15)
assert len(pdb_bytes) == uncomp_size, (
    f"Decompressed PDB size ({len(pdb_bytes)}) != declared size ({uncomp_size})"
)
assert b"https://raw.githubusercontent.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job/" in pdb_bytes, (
    "Source Link GitHub URL not found in decompressed embedded PDB!"
)
assert b"/_/" in pdb_bytes, "Deterministic path map prefix '/_/' not found in decompressed embedded PDB!"
assert b"StandHereSystem.cs" in pdb_bytes, "StandHereSystem.cs not found in decompressed embedded PDB!"
assert b"/home/" not in dll_bytes and b"/home/" not in pdb_bytes, "Unexpected /home/ path leaked!"
assert b"/build/" not in dll_bytes and b"/build/" not in pdb_bytes, "Unexpected /build/ path leaked!"
print(f"Embedded Portable PDB ({uncomp_size} bytes), Source Link, and deterministic path checks passed.")
PYEOF

decompiled=$("${nix_cmd[@]}" develop "$mod_root" --command ilspycmd "$dll_path")
if printf '%s' "$decompiled" | grep -q 'DisableOptimizations'; then
    printf 'ERROR: Release DLL has DisableOptimizations set!\n' >&2
    exit 1
fi
printf '==> ILSpy decompilation & Release optimization check passed: %s\n' "$dll_path"
