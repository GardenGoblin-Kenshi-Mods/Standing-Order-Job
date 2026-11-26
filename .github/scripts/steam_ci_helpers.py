#!/usr/bin/env python3
"""Safe, testable helpers for SteamCMD config.vdf handling and log sanitization."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

MAX_SECRET_BYTES = 49152  # GitHub Actions 48 KiB secret limit

AUTH_FAILURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("steam_guard_required", re.compile(r"steam\s+guard|two-factor|2fa|mobile\s+authenticator|enter\s+the\s+current\s+code", re.IGNORECASE)),
    ("cached_credentials_missing", re.compile(r"cached\s+credentials\s+not\s+found|no\s+cached\s+credentials", re.IGNORECASE)),
    ("invalid_password", re.compile(r"invalid\s+password|account\s+logon\s+denied|login\s+failure|invalid\s+login\s+auth\s+code", re.IGNORECASE)),
    ("rate_limited", re.compile(r"rate\s+limit\s+exceeded", re.IGNORECASE)),
    ("steamcmd_error", re.compile(r"\bERROR!\b|\bFAILED\s*\(", re.IGNORECASE)),
)

AUTH_SUCCESS_PATTERN = re.compile(
    r"(?:Logged\s+in\s+OK|Logging\s+in\s+user\s+['\"][^'\"]+['\"](?:\s+\[[^\]]+\])?\s+to\s+Steam\s+Public\.+\s*OK)",
    re.IGNORECASE,
)

UPLOAD_SUCCESS_PATTERN = re.compile(r"\bSuccess\.", re.IGNORECASE)
UPLOAD_FAILURE_PATTERN = re.compile(r"\bERROR!\b|Failed\s+to\s+update\s+workshop\s+item|Access\s+Denied", re.IGNORECASE)

# Matches KeyValues pairs inside a ConnectCache block so tokens can be masked/detected without logging.
CONNECT_CACHE_BLOCK_RE = re.compile(r'"ConnectCache"\s*\{([^}]*)\}', re.DOTALL)
KV_PAIR_RE = re.compile(r'"([^"]+)"\s+"([^"]+)"')
STEAM_ID3_RE = re.compile(r"\[U:\d+:\d+\]")
LONG_TOKEN_RE = re.compile(r"\b(?:[A-Fa-f0-9]{24,}|[A-Za-z0-9+/=_-]{36,})\b")


def extract_connect_cache_tokens(config_text: str) -> list[str]:
    """Return non-empty token values inside any ConnectCache KeyValues block."""
    tokens: list[str] = []
    for block in CONNECT_CACHE_BLOCK_RE.findall(config_text):
        for _key, value in KV_PAIR_RE.findall(block):
            if value:
                tokens.append(value)
    return tokens


def validate_config_vdf_text(config_text: str, max_bytes: int = MAX_SECRET_BYTES) -> tuple[int, int]:
    """Validate raw config.vdf text without revealing its contents.

    Returns (byte_length, token_count). Raises ValueError if invalid.
    """
    raw_bytes = config_text.encode("utf-8")
    byte_length = len(raw_bytes)
    if byte_length == 0:
        raise ValueError("config.vdf is empty.")
    if byte_length > max_bytes:
        raise ValueError(f"config.vdf size ({byte_length} bytes) exceeds limit ({max_bytes} bytes).")
    if '"InstallConfigStore"' not in config_text:
        raise ValueError("config.vdf does not look like a Valve InstallConfigStore VDF file.")
    tokens = extract_connect_cache_tokens(config_text)
    if not tokens:
        raise ValueError("config.vdf does not contain a non-empty ConnectCache session token.")
    return byte_length, len(tokens)


def write_secret_file_0600(dest: Path, raw_bytes: bytes) -> None:
    """Atomically create or overwrite dest with mode 0600 (no world-readable window)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, raw_bytes)
    finally:
        os.close(fd)


def restore_config_vdf(
    config_text: str,
    steam_home: Path,
    steamcmd_dir: Path | None = None,
    emit_gh_masks: bool = True,
) -> list[Path]:
    """Restore raw config.vdf with 0600 permissions to SteamCMD Linux config paths."""
    byte_length, _token_count = validate_config_vdf_text(config_text)
    raw_bytes = config_text.encode("utf-8")

    if emit_gh_masks:
        for token in extract_connect_cache_tokens(config_text):
            # Guard against GitHub Actions' multiline secret masking gap.
            print(f"::add-mask::{token}", flush=True)

    candidates: list[Path] = [
        steam_home / ".local" / "share" / "Steam" / "config" / "config.vdf",
        steam_home / ".steam" / "steam" / "config" / "config.vdf",
    ]
    if (steam_home / "Steam").is_dir():
        candidates.append(steam_home / "Steam" / "config" / "config.vdf")
    if steamcmd_dir is not None and (steamcmd_dir / "config").is_dir():
        candidates.append(steamcmd_dir / "config" / "config.vdf")

    written: list[Path] = []
    seen_real: set[Path] = set()
    for dest in candidates:
        parent_real = dest.parent.resolve() if dest.parent.exists() else dest.parent
        canonical = parent_real / dest.name
        if canonical in seen_real:
            continue
        seen_real.add(canonical)
        write_secret_file_0600(dest, raw_bytes)
        written.append(dest)

    print(f"Restored SteamCMD config.vdf ({byte_length} bytes, mode 0600) to {len(written)} path(s):")
    for path in written:
        print(f"  - {path}")
    return written


def sanitize_steam_log(log_text: str, username: str = "") -> list[str]:
    """Strip ANSI sequences, redact username and token-like strings, return safe lines."""
    ansi_re = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
    clean = ansi_re.sub("", log_text).replace("\r", "\n")
    safe_lines: list[str] = []
    for raw_line in clean.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if username:
            line = re.sub(re.escape(username), "<STEAM_USERNAME>", line, flags=re.IGNORECASE)
        line = STEAM_ID3_RE.sub("[U:1:<STEAMID>]", line)
        line = LONG_TOKEN_RE.sub("<REDACTED>", line)
        safe_lines.append(line)
    return safe_lines


def evaluate_auth_log(log_text: str, exit_code: int, username: str = "") -> tuple[bool, str, list[str]]:
    """Evaluate SteamCMD non-interactive login result.

    Returns (passed, reason, sanitized_lines).
    """
    safe_lines = sanitize_steam_log(log_text, username=username)
    joined = "\n".join(safe_lines)

    if exit_code == 124:
        return (
            False,
            "SteamCMD authentication timed out (likely waiting on Steam Guard or password prompt). "
            "Refresh STEAM_CONFIG_VDF with tools/steam-login.sh.",
            safe_lines,
        )

    for code, pattern in AUTH_FAILURE_PATTERNS:
        if pattern.search(joined):
            return (
                False,
                f"SteamCMD authentication failed ({code}, exit={exit_code}). "
                "Session may be expired, revoked, or rejected on this runner; refresh STEAM_CONFIG_VDF with tools/steam-login.sh.",
                safe_lines,
            )

    if exit_code != 0:
        return (
            False,
            f"SteamCMD exited nonzero (exit={exit_code}) without authenticated login confirmation.",
            safe_lines,
        )

    if not AUTH_SUCCESS_PATTERN.search(joined):
        return (
            False,
            "SteamCMD exited 0, but no authenticated login confirmation ('Logged in OK' / 'Logging in user ... OK') was observed.",
            safe_lines,
        )

    return True, "Authenticated SteamCMD non-interactive login succeeded.", safe_lines


def evaluate_upload_log(log_text: str, exit_code: int, username: str = "") -> tuple[bool, str, list[str]]:
    """Evaluate SteamCMD workshop_build_item result."""
    safe_lines = sanitize_steam_log(log_text, username=username)
    joined = "\n".join(safe_lines)

    if exit_code == 124:
        return False, "SteamCMD workshop_build_item timed out (exit=124).", safe_lines
    if UPLOAD_FAILURE_PATTERN.search(joined):
        return False, f"SteamCMD workshop_build_item reported an error (exit={exit_code}).", safe_lines
    if exit_code != 0:
        return False, f"SteamCMD workshop_build_item exited nonzero (exit={exit_code}).", safe_lines
    if not UPLOAD_SUCCESS_PATTERN.search(joined):
        return False, "SteamCMD workshop_build_item exited 0 without printing 'Success.'.", safe_lines
    return True, "SteamCMD workshop_build_item succeeded.", safe_lines


def write_github_output(output_path: str | None, pairs: dict[str, str]) -> None:
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        for key, value in pairs.items():
            single_line = value.replace("\r", " ").replace("\n", " ")
            handle.write(f"{key}={single_line}\n")


def cmd_inspect_config(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text(encoding="utf-8", errors="strict")
    tokens = extract_connect_cache_tokens(text)
    has_store = '"InstallConfigStore"' in text
    byte_length = len(text.encode("utf-8"))
    size_status = "within-48KiB-limit" if byte_length <= args.max_bytes else "EXCEEDS-48KiB-LIMIT"
    cache_status = f"ConnectCache=present({len(tokens)})" if tokens else "ConnectCache=missing"
    store_status = "InstallConfigStore=ok" if has_store else "InstallConfigStore=missing"
    print(f"{store_status}, {cache_status}, {size_status}")
    return 0


def cmd_restore_config(args: argparse.Namespace) -> int:
    config_text = os.environ.get("STEAM_CONFIG_VDF", "")
    if not config_text:
        print("::error::STEAM_CONFIG_VDF secret is empty or not configured.", file=sys.stderr)
        return 1
    try:
        restore_config_vdf(
            config_text=config_text,
            steam_home=Path(args.steam_home),
            steamcmd_dir=Path(args.steamcmd_dir) if args.steamcmd_dir else None,
            emit_gh_masks=not args.no_gh_masks,
        )
    except ValueError as exc:
        print(f"::error::Invalid STEAM_CONFIG_VDF: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_check_auth_log(args: argparse.Namespace) -> int:
    log_text = Path(args.log_file).read_text(encoding="utf-8", errors="replace")
    passed, reason, safe_lines = evaluate_auth_log(log_text, args.exit_code, username=args.username)
    print("Sanitized SteamCMD output (tail):")
    for line in safe_lines[-25:]:
        print(f"  {line}")
    status = "pass" if passed else "fail"
    write_github_output(args.github_output, {"auth_status": status, "auth_detail": reason})
    if passed:
        print(f"PASS: {reason}")
        return 0
    print(f"::error::{reason}", file=sys.stderr)
    return 1


def cmd_check_upload_log(args: argparse.Namespace) -> int:
    log_text = Path(args.log_file).read_text(encoding="utf-8", errors="replace")
    passed, reason, safe_lines = evaluate_upload_log(log_text, args.exit_code, username=args.username)
    print("Sanitized SteamCMD upload output (tail):")
    for line in safe_lines[-35:]:
        print(f"  {line}")
    status = "pass" if passed else "fail"
    write_github_output(args.github_output, {"upload_status": status, "upload_detail": reason})
    if passed:
        print(f"PASS: {reason}")
        return 0
    print(f"::error::{reason}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SteamCMD CI helper utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect-config", help="Inspect config.vdf metadata without printing contents")
    p_inspect.add_argument("--file", required=True)
    p_inspect.add_argument("--max-bytes", type=int, default=MAX_SECRET_BYTES)
    p_inspect.set_defaults(func=cmd_inspect_config)

    p_restore = sub.add_parser("restore-config", help="Restore STEAM_CONFIG_VDF env var to Steam config paths")
    p_restore.add_argument("--steam-home", required=True)
    p_restore.add_argument("--steamcmd-dir", default="")
    p_restore.add_argument("--no-gh-masks", action="store_true")
    p_restore.set_defaults(func=cmd_restore_config)

    p_auth = sub.add_parser("check-auth-log", help="Verify sanitized SteamCMD auth output")
    p_auth.add_argument("--log-file", required=True)
    p_auth.add_argument("--exit-code", type=int, required=True)
    p_auth.add_argument("--username", default="")
    p_auth.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    p_auth.set_defaults(func=cmd_check_auth_log)

    p_upload = sub.add_parser("check-upload-log", help="Verify sanitized SteamCMD workshop_build_item output")
    p_upload.add_argument("--log-file", required=True)
    p_upload.add_argument("--exit-code", type=int, required=True)
    p_upload.add_argument("--username", default="")
    p_upload.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    p_upload.set_defaults(func=cmd_check_upload_log)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
