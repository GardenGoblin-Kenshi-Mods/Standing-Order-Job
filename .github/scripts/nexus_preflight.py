#!/usr/bin/env python3
"""Fail-closed Nexus Mods v3 API duplicate-version preflight check."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_GAME_DOMAIN = "kenshi"
API_BASE = "https://api.nexusmods.com/v3"
TIMEOUT_SECONDS = 30
UINT32_MASK = (1 << 32) - 1


def normalize_version(version: str) -> str:
    return version.strip().lstrip("vV").lower()


def extract_web_mod_id(mod_id: str) -> str:
    """Convert a Nexus v3 global mod ID ((game_id << 32) | game_scoped_id) to its web URL ID."""
    cleaned = mod_id.strip()
    if cleaned.isdigit():
        numeric = int(cleaned)
        if numeric >= (1 << 32):
            return str(numeric & UINT32_MASK)
    return cleaned


def fetch_mod_file_versions_json(file_id: str, api_key: str) -> dict:
    encoded_file_id = urllib.parse.quote(file_id, safe="")
    url = f"{API_BASE}/mod-files/{encoded_file_id}/versions"
    req = urllib.request.Request(
        url,
        headers={
            "apikey": api_key.strip(),
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
        # Never read or log the error body to avoid echoing sensitive/credential-adjacent payloads.
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


def _check_pagination(container: dict, label: str) -> None:
    """Fail closed if Nexus adds pagination or truncation fields."""
    for page_key in ("next", "next_page", "next_cursor"):
        if container.get(page_key):
            raise RuntimeError(
                f"Nexus API {label} indicates paginated results ({page_key!r} is set); failing closed."
            )
    if container.get("has_more") is True:
        raise RuntimeError(f"Nexus API {label} indicates truncated results ('has_more' is true); failing closed.")


def find_matching_versions(payload: dict, target_version: str) -> tuple[int, list[dict]]:
    """Validate Nexus v3 /mod-files/{id}/versions payload and return (total_versions, matching_entries)."""
    _check_pagination(payload, "response")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Nexus API response is missing a 'data' object (ambiguous data).")

    _check_pagination(data, "response data")

    versions = data.get("versions")
    if not isinstance(versions, list):
        raise RuntimeError("Nexus API response is missing a 'data.versions' list (ambiguous data).")

    target_norm = normalize_version(target_version)
    if not target_norm:
        raise RuntimeError(f"Target version is empty: {target_version!r}")

    matches: list[dict] = []
    for idx, entry in enumerate(versions):
        if not isinstance(entry, dict):
            raise RuntimeError(f"Nexus API version entry #{idx} is not a JSON object (ambiguous data).")
        raw_version = entry.get("version")
        if not isinstance(raw_version, str) or not raw_version.strip():
            raise RuntimeError(f"Nexus API version entry #{idx} is missing a string 'version' field (ambiguous data).")
        if normalize_version(raw_version) == target_norm:
            matches.append(entry)

    return len(versions), matches


def write_github_output(output_path: str | None, pairs: dict[str, str]) -> None:
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        for key, value in pairs.items():
            single = value.replace("\r", " ").replace("\n", " ")
            handle.write(f"{key}={single}\n")


def run_preflight(
    game_domain: str,
    file_id: str,
    target_version: str,
    api_key: str,
    dry_run: bool,
    mod_id: str = "",
    fetcher=fetch_mod_file_versions_json,
) -> tuple[int, dict[str, str], str]:
    """Run Nexus v3 duplicate-version preflight. Returns (exit_code, outputs, message)."""
    cleaned_file_id = file_id.strip()
    cleaned_mod_id = mod_id.strip()
    cleaned_api_key = api_key.strip()
    web_mod_id = extract_web_mod_id(cleaned_mod_id)
    display_ver = f"v{normalize_version(target_version)}" if normalize_version(target_version) else target_version

    base_outputs = {
        "preflight_status": "",
        "preflight_detail": "",
        "existing_version_id": "",
        "existing_file_id": "",
        "web_mod_id": web_mod_id,
    }

    if not cleaned_file_id or not cleaned_api_key:
        if dry_run:
            msg = (
                "Skipped credentialed Nexus API v3 duplicate-version preflight in dry-run "
                "(requires NEXUSMODS_API_KEY and NEXUSMODS_FILE_ID)."
            )
            return 0, {**base_outputs, "preflight_status": "skipped_no_credentials", "preflight_detail": msg}, msg
        msg = "Nexus preflight failed: missing NEXUSMODS_FILE_ID or NEXUSMODS_API_KEY; no upload attempted."
        return 1, {**base_outputs, "preflight_status": "error", "preflight_detail": msg}, msg

    try:
        payload = fetcher(cleaned_file_id, cleaned_api_key)
        total_versions, matches = find_matching_versions(payload, target_version)
    except RuntimeError as exc:
        msg = f"Nexus preflight failed (fail closed; no upload attempted): {exc}"
        return 1, {**base_outputs, "preflight_status": "error", "preflight_detail": msg}, msg

    if matches:
        first = matches[0]
        version_id = str(first.get("id") or "")
        game_scoped_file_id = str(first.get("game_scoped_id") or "")
        category = str(first.get("category") or "unknown")
        file_url = ""
        if web_mod_id:
            if game_scoped_file_id:
                file_url = (
                    f"https://www.nexusmods.com/{game_domain}/mods/{web_mod_id}"
                    f"?tab=files&file_id={game_scoped_file_id}"
                )
            else:
                file_url = f"https://www.nexusmods.com/{game_domain}/mods/{web_mod_id}?tab=files"
        url_suffix = f", {file_url}" if file_url else ""
        msg = (
            f"Nexus publish blocked: version {display_ver} already exists for mod file {cleaned_file_id} "
            f"(version_id={version_id or 'unknown'}, category={category}{url_suffix}); no upload attempted."
        )
        outputs = {
            **base_outputs,
            "preflight_status": "blocked_duplicate",
            "preflight_detail": msg,
            "existing_version_id": version_id,
            "existing_file_id": cleaned_file_id,
            "existing_file_url": file_url,
        }
        return (0 if dry_run else 1), outputs, msg

    msg = (
        f"Version {display_ver} does not yet exist on Nexus Mods mod file {cleaned_file_id} "
        f"(checked {total_versions} existing version(s) via v3 API)."
    )
    return (
        0,
        {
            **base_outputs,
            "preflight_status": "available",
            "preflight_detail": msg,
            "existing_file_url": "",
        },
        msg,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check Nexus Mods v3 API for an existing mod file version before upload"
    )
    parser.add_argument("--game-domain", default=DEFAULT_GAME_DOMAIN)
    parser.add_argument("--file-id", default="")
    parser.add_argument("--mod-id", default="")
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = parser.parse_args(argv)

    api_key = os.environ.get("NEXUSMODS_API_KEY", "").strip()
    rc, outputs, msg = run_preflight(
        game_domain=args.game_domain,
        file_id=args.file_id,
        target_version=args.target_version,
        api_key=api_key,
        dry_run=args.dry_run,
        mod_id=args.mod_id,
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
