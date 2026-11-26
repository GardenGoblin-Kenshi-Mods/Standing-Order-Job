#!/usr/bin/env bash
# Regenerate nix/deps.json from the pinned MeitouClient flake input using nuget-to-json.
set -euo pipefail

if ! command -v nix >/dev/null 2>&1; then
    printf 'nix is required to run tools/update-nix-deps.sh\n' >&2
    exit 1
fi

mod_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT

nix_cmd=(nix --extra-experimental-features 'nix-command flakes')

"${nix_cmd[@]}" flake lock "$mod_root"

meitou_store=$(python3 - "$mod_root" <<'PYEOF'
import json
import subprocess
import sys

mod_root = sys.argv[1]
raw = subprocess.check_output(
    ["nix", "--extra-experimental-features", "nix-command flakes", "flake", "archive", "--json", mod_root],
    text=True,
)
data = json.loads(raw)
print(data["inputs"]["meitou-client"]["path"])
PYEOF
)

cp -R "$meitou_store" "$tmp_dir/MeitouClient"
cp -R "$mod_root/src" "$tmp_dir/src"
cp -R "$mod_root/tools" "$tmp_dir/tools"
cp "$mod_root/version.txt" "$tmp_dir/version.txt"
cp "$mod_root/.editorconfig" "$tmp_dir/.editorconfig"
chmod -R u+w "$tmp_dir"
rm -rf "$tmp_dir/src/obj" "$tmp_dir/src/bin" "$tmp_dir/tools/obj" "$tmp_dir/tools/bin"

mkdir -p "$tmp_dir/packages" "$tmp_dir/home"
export HOME="$tmp_dir/home"
export DOTNET_CLI_HOME="$tmp_dir/dotnet-cli"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_NOLOGO=1
export DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1

"${nix_cmd[@]}" develop "$mod_root" --command bash -c '
  set -euo pipefail
  dotnet restore "$1/src/StandingOrderJob.csproj" --packages "$1/packages" -p:MeitouClientRoot="$1/MeitouClient"
  dotnet restore "$1/tools/StandingOrderJobPackage.csproj" --packages "$1/packages" -p:MeitouClientRoot="$1/MeitouClient"
  nuget-to-json "$1/packages" > "$2/nix/deps.json"
' _ "$tmp_dir" "$mod_root"

printf 'Updated %s/nix/deps.json\n' "$mod_root"
