#!/usr/bin/env bash
set -euo pipefail

: "${MEITOU_CLIENT_ROOT:?Set MEITOU_CLIENT_ROOT to the MeitouClient checkout.}"

mod_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
out="$mod_root/out/StandingOrderJob"
source_project="$mod_root/src/StandingOrderJob.csproj"
package_project="$mod_root/tools/StandingOrderJobPackage.csproj"

rm -rf "$out"
mkdir -p "$out"

dotnet build -c Release "$source_project" -p:MeitouClientRoot="$MEITOU_CLIENT_ROOT"
dotnet run -c Release --project "$package_project" -p:MeitouClientRoot="$MEITOU_CLIENT_ROOT" -- "$out/StandingOrderJob.mod"
cp "$mod_root/meitou.json" "$out/meitou.json"
cp "$mod_root/src/bin/Release/net11.0/StandingOrderJob.dll" "$out/StandingOrderJob.dll"

printf 'Built %s\n' "$out"
