#!/usr/bin/env bash
# Scan a gzipped MSBuild .binlog for leaked credentials/tokens or host /home/ paths.
set -euo pipefail

mod_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
binlog_path="${1:-$mod_root/out/StandingOrderJob.binlog}"

if [[ ! -f "$binlog_path" ]]; then
    printf 'Binary log not found: %s\n' "$binlog_path" >&2
    exit 1
fi

python3 - "$binlog_path" <<'PYEOF'
import gzip
import re
import sys
from pathlib import Path

binlog = Path(sys.argv[1])
with gzip.open(binlog, "rb") as f:
    raw = f.read()

# Search UTF-8, UTF-16LE, and raw ASCII representations so no string encoding hides a match.
candidates = [
    ("raw-utf8", raw.decode("utf-8", errors="ignore")),
    ("raw-utf16le", raw.decode("utf-16-le", errors="ignore")),
]

patterns = [
    ("GitHub classic PAT", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")),
    ("GitHub fine-grained PAT", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("GITHUB_TOKEN assignment", re.compile(r"GITHUB_TOKEN=[^\s\x00]{4,}")),
    ("GH_TOKEN assignment", re.compile(r"GH_TOKEN=[^\s\x00]{4,}")),
    ("NEXUSMODS_API_KEY assignment", re.compile(r"NEXUSMODS_API_KEY=[^\s\x00]{4,}")),
    ("RELEASE_PLEASE_TOKEN assignment", re.compile(r"RELEASE_PLEASE_TOKEN=[^\s\x00]{4,}")),
    ("MOD_DEPS_TOKEN assignment", re.compile(r"MOD_DEPS_TOKEN=[^\s\x00]{4,}")),
    ("ACTIONS_RUNTIME_TOKEN assignment", re.compile(r"ACTIONS_RUNTIME_TOKEN=[^\s\x00]{4,}")),
    ("ACTIONS_ID_TOKEN_REQUEST_TOKEN assignment", re.compile(r"ACTIONS_ID_TOKEN_REQUEST_TOKEN=[^\s\x00]{4,}")),
    ("Host /home/ path", re.compile(r"/home/(?!\.)[A-Za-z0-9._-]+")),
]

findings: list[str] = []
for encoding_name, text in candidates:
    for label, regex in patterns:
        for match in regex.finditer(text):
            snippet = match.group(0)
            if label == "Host /home/ path" and snippet == "/home/meitou":
                continue
            findings.append(f"[{encoding_name}] {label}: {snippet[:64]}")

if findings:
    print("Secret/path hygiene check FAILED:", file=sys.stderr)
    for item in sorted(set(findings))[:20]:
        print(f"  - {item}", file=sys.stderr)
    raise SystemExit(1)

print(f"Binlog hygiene check passed ({binlog.name}, {len(raw)} bytes decompressed).")
PYEOF
