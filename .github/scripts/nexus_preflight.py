#!/usr/bin/env python3
"""Fail-closed Nexus Mods API duplicate-version preflight check."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_GAME_DOMAIN = "kenshi"
API_BASE = "https://api.nexusmods.com/v1"
TIMEOUT_SECONDS = 30


def normalize_version(version: str) -> str:
    return version.strip().lstrip("vV").lower()


def fetch_mod_files_json(game_domain: str, mod_id: str, api_key: str) -> dict:
    url = f"{API_BASE}/games/{game_domain}/mods/{mod_id}/files.json"
    req = urllib.request.Request(
        url,
        headers={
            "apikey": api_key,
            "accept": "application/json",
            "User-Agent": "StandingOrderJob-CI/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise RuntimeError(f"Nexus API returned unexpected HTTP status {status}")
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Nexus API HTTP error {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Nexus API network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Nexus API request timed out after {TIMEOUT_SECONDS}s") from exc

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Nexus API returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError("Nexus API response root is not a JSON object.")
    return parsed


def find_matching_versions(payload: dict, target_version: str) -> tuple[int, list[dict]]:
    """Validate Nexus files.json payload and return (total_files, matching_entries)."""
    if "files" not in payload or not isinstance(payload["files"], list):
        raise RuntimeError("Nexus API response is missing a 'files' list (ambiguous data).")

    # Fail closed if Nexus ever adds pagination flags indicating an incomplete listing.
    for page_key in ("next", "next_page", "next_cursor"):
        if payload.get(page_key):
            raise RuntimeError(f"Nexus API response indicates paginated results ({page_key!r} is set); failing closed.")
    if payload.get("has_more") is True:
        raise RuntimeError("Nexus API response indicates truncated results ('has_more' is true); failing closed.")

    target_norm = normalize_version(target_version)
    if not target_norm:
        raise RuntimeError(f"Target version is empty: {target_version!r}")

    matches: list[dict] = []
    files = payload["files"]
    for idx, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise RuntimeError(f"Nexus API file entry #{idx} is not a JSON object (ambiguous data).")
        raw_version = entry.get("version")
        if not isinstance(raw_version, str) or not raw_version.strip():
            raise RuntimeError(f"Nexus API file entry #{idx} is missing a string 'version' field (ambiguous data).")
        if normalize_version(raw_version) == target_norm:
            matches.append(entry)

    return len(files), matches


def write_github_output(output_path: str | None, pairs: dict[str, str]) -> None:
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        for key, value in pairs.items():
            single = value.replace("\r", " ").replace("\n", " ")
            handle.write(f"{key}={single}\n")


def run_preflight(
    game_domain: str,
    mod_id: str,
    target_version: str,
    api_key: str,
    dry_run: bool,
    fetcher=fetch_mod_files_json,
) -> tuple[int, dict[str, str], str]:
    """Run Nexus duplicate-version preflight. Returns (exit_code, outputs, message)."""
    cleaned_mod_id = mod_id.strip()
    display_ver = f"v{normalize_version(target_version)}" if normalize_version(target_version) else target_version

    if not cleaned_mod_id or not api_key:
        if dry_run:
            msg = (
                "Skipped credentialed Nexus API duplicate-version preflight in dry-run "
                "(requires NEXUSMODS_API_KEY and NEXUSMODS_MOD_ID)."
            )
            return 0, {"preflight_status": "skipped_no_credentials", "preflight_detail": msg, "existing_file_id": ""}, msg
        msg = "Nexus preflight failed: missing NEXUSMODS_MOD_ID or NEXUSMODS_API_KEY; no upload attempted."
        return 1, {"preflight_status": "error", "preflight_detail": msg, "existing_file_id": ""}, msg

    try:
        payload = fetcher(game_domain, cleaned_mod_id, api_key)
        total_files, matches = find_matching_versions(payload, target_version)
    except RuntimeError as exc:
        msg = f"Nexus preflight failed (fail closed; no upload attempted): {exc}"
        return 1, {"preflight_status": "error", "preflight_detail": msg, "existing_file_id": ""}, msg

    if matches:
        first = matches[0]
        file_id = str(first.get("file_id") or "")
        category = str(first.get("category_name") or first.get("category_id") or "unknown")
        file_url = (
            f"https://www.nexusmods.com/{game_domain}/mods/{cleaned_mod_id}?tab=files&file_id={file_id}"
            if file_id
            else f"https://www.nexusmods.com/{game_domain}/mods/{cleaned_mod_id}?tab=files"
        )
        msg = (
            f"Nexus publish blocked: version {display_ver} already exists for this mod "
            f"(file_id={file_id or 'unknown'}, category={category}, {file_url}); no upload attempted."
        )
        outputs = {
            "preflight_status": "blocked_duplicate",
            "preflight_detail": msg,
            "existing_file_id": file_id,
            "existing_file_url": file_url,
        }
        return (0 if dry_run else 1), outputs, msg

    msg = f"Version {display_ver} does not yet exist on Nexus Mods (checked {total_files} existing file(s))."
    return (
        0,
        {
            "preflight_status": "available",
            "preflight_detail": msg,
            "existing_file_id": "",
            "existing_file_url": "",
        },
        msg,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Nexus Mods API for existing mod file version before upload")
    parser.add_argument("--game-domain", default=DEFAULT_GAME_DOMAIN)
    parser.add_argument("--mod-id", default="")
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = parser.parse_args(argv)

    api_key = os.environ.get("NEXUSMODS_API_KEY", "")
    rc, outputs, msg = run_preflight(
        game_domain=args.game_domain,
        mod_id=args.mod_id,
        target_version=args.target_version,
        api_key=api_key,
        dry_run=args.dry_run,
    )
    write_github_output(args.github_output, outputs)

    if rc == 0:
        prefix = "DRY-RUN PREFLIGHT: " if args.dry_run else "PASS: "
        print(f"{prefix}{msg}")
    else:
        print(f"::error::{msg}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
