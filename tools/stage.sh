#!/usr/bin/env bash
set -euo pipefail

: "${KENSHI_PATH:?Set KENSHI_PATH to the Kenshi install.}"

mod_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
package="$mod_root/out/StandingOrderJob"
staging="$mod_root/out/staging"
target="$KENSHI_PATH/mods/StandingOrderJob"

if [[ ! -f "$package/StandingOrderJob.mod" || ! -f "$package/meitou.json" || ! -f "$package/StandingOrderJob.dll" ]]; then
    printf 'Package is incomplete. Run ./tools/package.sh first.\n' >&2
    exit 1
fi

if [[ -e "$target" && ! -L "$target" ]]; then
    printf 'Refusing to replace existing non-symlink path: %s\n' "$target" >&2
    exit 1
fi

if [[ -L "$target" && "$(readlink -f "$target")" != "$package" ]]; then
    printf 'Refusing to replace symlink owned by another staging path: %s\n' "$target" >&2
    exit 1
fi

mkdir -p "$KENSHI_PATH/mods" "$staging"
ln -sfn "$package" "$target"
printf 'StandingOrderJob.mod\n' > "$staging/mods.cfg"
printf 'Staged %s\n' "$target"
printf 'Load order: %s\n' "$staging/mods.cfg"
