#!/usr/bin/env bash
set -euo pipefail

: "${MEITOU_CLIENT_ROOT:?Set MEITOU_CLIENT_ROOT to the MeitouClient checkout.}"

mod_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
test_project="$mod_root/tests/StandingOrderJob.Tests/StandingOrderJob.Tests.csproj"

dotnet test "$test_project" -c Release -m:1 -p:MeitouClientRoot="$MEITOU_CLIENT_ROOT" "$@"
