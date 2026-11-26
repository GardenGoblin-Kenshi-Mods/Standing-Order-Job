#!/usr/bin/env python3
"""Shared GitHub Release resolution, SHA-256 verification, safe ZIP staging, and Steam VDF generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

PACKAGE_NAME = "StandingOrderJob"
DISPLAY_NAME = "Standing Order Job"
KENSHI_APP_ID = "233860"
REPO_URL = "https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job"
REQUIRED_ARCHIVE_FILES: frozenset[str] = frozenset(
    {
        f"{PACKAGE_NAME}/{PACKAGE_NAME}.dll",
        f"{PACKAGE_NAME}/{PACKAGE_NAME}.mod",
        f"{PACKAGE_NAME}/meitou.json",
    }
)
ALLOWED_PREVIEW_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif"})
MAX_PREVIEW_BYTES = 1024 * 1024  # Steam Workshop 1 MiB preview image cap
MAX_CHANGENOTE_CHARS = 4000
SHA256_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+)$")
POSITIVE_INT_RE = re.compile(r"^[1-9][0-9]*$")


def write_github_output(output_path: str | None, pairs: dict[str, str]) -> None:
    """Append key=value or multiline heredoc entries to GITHUB_OUTPUT."""
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        for key, value in pairs.items():
            if "\n" in value or "\r" in value:
                eof = f"EOF_{secrets.token_hex(16)}"
                handle.write(f"{key}<<{eof}\n{value}\n{eof}\n")
            else:
                handle.write(f"{key}={value}\n")


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sha256_manifest(path: Path) -> dict[str, str]:
    """Parse a standard sha256sum manifest into {filename: lowercase_sha256}."""
    entries: dict[str, str] = {}
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"Checksum manifest is empty: {path}")
    for line in lines:
        match = SHA256_LINE_RE.match(line)
        if not match:
            raise ValueError(f"Malformed checksum line in {path.name}: {line!r}")
        expected_hash = match.group(1).lower()
        target_name = match.group(2).strip()
        if target_name in entries:
            raise ValueError(f"Duplicate entry in {path.name}: {target_name!r}")
        entries[target_name] = expected_hash
    return entries


def validate_zip_entries(zip_path: Path) -> list[zipfile.ZipInfo]:
    """Inspect ZIP entries before extraction; reject traversal, symlinks, and unexpected paths."""
    file_infos: list[zipfile.ZipInfo] = []
    seen_files: set[str] = set()

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            name = info.filename
            if not name:
                raise ValueError("ZIP contains an empty entry name.")
            if "\\" in name:
                raise ValueError(f"ZIP contains unsafe backslash path: {name!r}")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                raise ValueError(f"ZIP contains absolute path: {name!r}")

            mode = (info.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                raise ValueError(f"ZIP contains forbidden symlink entry: {name!r}")

            normalized = posixpath.normpath(name)
            parts = normalized.split("/")
            if normalized in (".", "..") or ".." in parts or normalized.startswith("/"):
                raise ValueError(f"ZIP contains path traversal entry: {name!r}")

            if info.is_dir():
                if normalized != PACKAGE_NAME:
                    raise ValueError(f"ZIP contains unexpected directory entry: {name!r}")
                continue

            if name != normalized:
                raise ValueError(f"ZIP contains non-canonical file path: {name!r}")
            if name in seen_files:
                raise ValueError(f"ZIP contains duplicate file entry: {name!r}")
            if name not in REQUIRED_ARCHIVE_FILES:
                raise ValueError(f"ZIP contains unexpected file entry: {name!r}")

            seen_files.add(name)
            file_infos.append(info)

    missing = sorted(REQUIRED_ARCHIVE_FILES - seen_files)
    if missing:
        raise ValueError(f"ZIP is missing required package file(s): {missing}")

    return file_infos


def list_all_git_tags(git_dir: Path) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(git_dir), "tag", "--list"],
        capture_output=True,
        text=True,
        check=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def resolve_git_tag_commit(git_dir: Path, tag_name: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(git_dir), "rev-list", "-n", "1", "--", tag_name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def fetch_github_release(repo: str, tag_name: str | None) -> tuple[int, dict | None, str]:
    fields = "tagName,url,name,body,assets,isDraft,isPrerelease,targetCommitish"
    cmd = ["gh", "release", "view"]
    if tag_name is not None:
        cmd.append(tag_name)
    cmd.extend(["--repo", repo, "--json", fields])
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return result.returncode, None, result.stderr.strip()
    return 0, json.loads(result.stdout), ""


def resolve_release(
    repo: str,
    release_tag: str,
    git_dir: Path,
    require_contents_sha: bool,
) -> dict[str, object]:
    """Resolve 'latest'/blank or an exact Git tag to a published GitHub Release with required assets."""
    if "\x00" in release_tag or "\n" in release_tag or "\r" in release_tag:
        raise ValueError(f"Release tag not found: '{release_tag}'")

    is_latest = release_tag == "" or release_tag == "latest"
    if is_latest:
        rc, release, err = fetch_github_release(repo, None)
        if rc != 0 or release is None or release.get("isDraft"):
            raise ValueError(f"No published GitHub Release found in {repo}: {err or 'latest release unavailable'}")
        tag_name = str(release.get("tagName") or "")
        if not tag_name:
            raise ValueError(f"Resolved latest GitHub Release in {repo} has an empty tagName.")
    else:
        all_tags = list_all_git_tags(git_dir)
        if release_tag not in all_tags:
            raise ValueError(f"Release tag not found: '{release_tag}'")

        rc, release, _err = fetch_github_release(repo, release_tag)
        if rc != 0 or release is None or release.get("isDraft") or release.get("tagName") != release_tag:
            raise ValueError(f"Tag '{release_tag}' exists, but no published GitHub Release was found")
        tag_name = release_tag

    version = tag_name[1:] if tag_name.startswith("v") else tag_name
    zip_name = f"{PACKAGE_NAME}-{version}.zip"
    zip_sha_name = f"{zip_name}.sha256"
    contents_sha_name = f"{PACKAGE_NAME}-{version}.contents.sha256"

    asset_names = {str(a.get("name", "")) for a in release.get("assets", [])}
    missing_assets: list[str] = []
    if zip_name not in asset_names:
        missing_assets.append(zip_name)
    if zip_sha_name not in asset_names:
        missing_assets.append(zip_sha_name)
    if require_contents_sha and contents_sha_name not in asset_names:
        missing_assets.append(contents_sha_name)
    if missing_assets:
        raise ValueError(
            f"Published GitHub Release '{tag_name}' is missing required release asset(s): {', '.join(missing_assets)}"
        )

    commit_sha = resolve_git_tag_commit(git_dir, tag_name) or str(release.get("targetCommitish") or "")
    release_url = str(release.get("url") or f"https://github.com/{repo}/releases/tag/{tag_name}")
    changelog = (str(release.get("body") or "")).strip() or f"Release {tag_name}"

    return {
        "tag_name": tag_name,
        "release_url": release_url,
        "commit_sha": commit_sha,
        "version": version,
        "zip_name": zip_name,
        "zip_sha_name": zip_sha_name,
        "contents_sha_name": contents_sha_name if contents_sha_name in asset_names else "",
        "changelog": changelog,
        "release": release,
    }


def verify_zip_artifact(zip_path: Path, zip_sha_path: Path, expected_tag: str) -> dict[str, str]:
    """Fail-closed SHA-256, safe ZIP structure, and meitou.json version verification."""
    if not zip_path.is_file():
        raise ValueError(f"Downloaded ZIP archive is missing: {zip_path}")
    if not zip_sha_path.is_file():
        raise ValueError(f"Downloaded ZIP checksum manifest is missing: {zip_sha_path}")

    zip_entries = parse_sha256_manifest(zip_sha_path)
    if len(zip_entries) != 1 or zip_path.name not in zip_entries:
        raise ValueError(
            f"Checksum manifest {zip_sha_path.name} must contain exactly one entry for {zip_path.name!r}; "
            f"found: {sorted(zip_entries)}"
        )

    expected_sha = zip_entries[zip_path.name]
    actual_sha = compute_sha256(zip_path)

    print(f"Downloaded ZIP: {zip_path.resolve()}")
    print(f"Expected ZIP SHA-256: {expected_sha}")
    print(f"Computed ZIP SHA-256: {actual_sha}")

    sha_proc = subprocess.run(
        ["sha256sum", "-c", str(zip_sha_path.resolve())],
        cwd=str(zip_path.parent.resolve()),
        capture_output=True,
        text=True,
        check=False,
    )
    if sha_proc.stdout.strip():
        print(sha_proc.stdout.strip())
    if sha_proc.returncode != 0 or actual_sha != expected_sha:
        if sha_proc.stderr.strip():
            print(sha_proc.stderr.strip(), file=sys.stderr)
        raise ValueError(
            f"ZIP SHA-256 verification FAILED for {zip_path.name}: expected {expected_sha}, computed {actual_sha}"
        )
    print("ZIP SHA-256 verification: PASS")

    validate_zip_entries(zip_path)

    expected_version = expected_tag[1:] if expected_tag.startswith("v") else expected_tag
    with zipfile.ZipFile(zip_path, "r") as zf:
        with zf.open(f"{PACKAGE_NAME}/meitou.json") as meta_file:
            meta = json.load(meta_file)

    version = str(meta.get("version", "")).strip()
    if version != expected_version:
        raise ValueError(f"meitou.json version ({version!r}) does not match release tag ({expected_tag!r}).")

    game_version = str(meta.get("gameVersion", "")).strip()
    description = (
        f"MeitouClient code mod ({game_version}). "
        f"Verify provenance/checksums: {REPO_URL}#build-transparency--verification"
    )
    if len(description) > 255:
        description = description[:252] + "..."

    return {
        "zip_path": str(zip_path.resolve()),
        "zip_name": zip_path.name,
        "zip_sha256": actual_sha,
        "version": version,
        "game_version": game_version,
        "display_name": DISPLAY_NAME,
        "description": description,
    }


def stage_and_verify_steam_content(
    zip_path: Path,
    zip_sha256: str,
    contents_sha_path: Path,
    staging_dir: Path,
) -> dict[str, str]:
    """Safely extract verified ZIP and verify every extracted runtime file against .contents.sha256."""
    if not zip_path.is_file():
        raise ValueError(f"Verified ZIP is missing: {zip_path}")
    if not contents_sha_path.is_file():
        raise ValueError(f"Published contents checksum manifest is missing: {contents_sha_path}")

    contents_entries = parse_sha256_manifest(contents_sha_path)
    actual_manifest_paths = set(contents_entries.keys())
    if actual_manifest_paths != REQUIRED_ARCHIVE_FILES:
        missing = sorted(REQUIRED_ARCHIVE_FILES - actual_manifest_paths)
        extra = sorted(actual_manifest_paths - REQUIRED_ARCHIVE_FILES)
        raise ValueError(
            f"Contents manifest {contents_sha_path.name} does not match expected runtime files "
            f"(missing={missing}, unexpected={extra})."
        )

    extract_dir = (staging_dir / "extract").resolve()
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    print(f"Extracting verified ZIP: {zip_path.resolve()} (SHA-256: {zip_sha256}) -> {extract_dir}")

    file_infos = validate_zip_entries(zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in file_infos:
            dest = (extract_dir / info.filename).resolve()
            try:
                dest.relative_to(extract_dir)
            except ValueError as exc:
                raise ValueError(f"Extracted destination escapes staging directory: {dest}") from exc
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(info.filename)
            dest.write_bytes(data)
            os.chmod(dest, 0o644)

    # Confirm exact file set on disk (zero unexpected files, zero symlinks).
    extracted_files: set[str] = set()
    for root_str, dirnames, filenames in os.walk(extract_dir, followlinks=False):
        root_path = Path(root_str)
        for dirname in dirnames:
            dpath = root_path / dirname
            if dpath.is_symlink():
                raise ValueError(f"Unexpected symlink directory in staging tree: {dpath}")
        for filename in filenames:
            fpath = root_path / filename
            if fpath.is_symlink():
                raise ValueError(f"Unexpected symlink file in staging tree: {fpath}")
            extracted_files.add(fpath.relative_to(extract_dir).as_posix())

    if extracted_files != REQUIRED_ARCHIVE_FILES:
        missing = sorted(REQUIRED_ARCHIVE_FILES - extracted_files)
        extra = sorted(extracted_files - REQUIRED_ARCHIVE_FILES)
        raise ValueError(
            f"Extracted staging tree mismatch (missing={missing}, unexpected={extra})."
        )

    sha_proc = subprocess.run(
        ["sha256sum", "-c", str(contents_sha_path.resolve())],
        cwd=str(extract_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if sha_proc.stdout.strip():
        print("Extracted file SHA-256 verification:")
        for line in sha_proc.stdout.strip().splitlines():
            print(f"  {line}")
    if sha_proc.returncode != 0:
        if sha_proc.stderr.strip():
            print(sha_proc.stderr.strip(), file=sys.stderr)
        raise ValueError("Extracted file verification FAILED against published .contents.sha256 manifest.")

    # MeitouClient's ModList.Discover scans top-level *.mod files inside workshop/content/233860/<item_id>/.
    # Point SteamCMD contentfolder at extract/StandingOrderJob so the three runtime files sit at item root.
    content_folder = (extract_dir / PACKAGE_NAME).resolve()
    for required_rel in sorted(REQUIRED_ARCHIVE_FILES):
        leaf = required_rel.split("/", 1)[1]
        if not (content_folder / leaf).is_file():
            raise ValueError(f"Workshop contentfolder missing expected file at root: {content_folder / leaf}")

    print(
        "Integrity check PASS: all 3 extracted runtime files match the published .contents.sha256 manifest. "
        "(Note: SHA-256 manifests verify integrity against the published release; build provenance is "
        "attested separately via GitHub build-provenance attestations.)"
    )
    print(f"Staged Workshop contentfolder: {content_folder}")

    return {
        "extract_dir": str(extract_dir),
        "content_folder": str(content_folder),
        "contents_check_output": sha_proc.stdout.strip(),
    }


def escape_vdf_value(text: str) -> str:
    """Escape a string value for Valve KeyValues double-quoted syntax."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    escaped = normalized.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return escaped


def format_changenote(changelog: str, tag: str, release_url: str, max_chars: int = MAX_CHANGENOTE_CHARS) -> str:
    body = changelog.replace("\r\n", "\n").replace("\r", "\n").strip()
    footer = f"Release: {tag} ({release_url})"
    if not body:
        note = footer
    elif release_url in body:
        note = body
    else:
        note = f"{body}\n\n{footer}"

    if len(note) > max_chars:
        suffix = f"\n...\nFull changelog: {release_url}"
        keep = max(0, max_chars - len(suffix))
        note = note[:keep].rstrip() + suffix
    return note


def parse_simple_vdf(vdf_text: str) -> tuple[str, dict[str, str]]:
    """Parse our flat generated Valve KeyValues workshopitem block back to verify syntax."""
    lines = [line.strip() for line in vdf_text.splitlines() if line.strip()]
    if len(lines) < 3 or lines[1] != "{" or lines[-1] != "}":
        raise ValueError("Generated VDF does not have expected root { ... } structure.")

    root_match = re.fullmatch(r'"([^"]+)"', lines[0])
    if not root_match:
        raise ValueError(f"Generated VDF has malformed root header: {lines[0]!r}")
    root_name = root_match.group(1)

    kv_re = re.compile(r'^"([^"\\]+)"\s+"((?:[^"\\]|\\.)*)"$')
    values: dict[str, str] = {}
    for line in lines[2:-1]:
        match = kv_re.match(line)
        if not match:
            raise ValueError(f"Generated VDF has malformed KeyValues entry: {line!r}")
        key, val = match.group(1), match.group(2)
        if key in values:
            raise ValueError(f"Generated VDF has duplicate key: {key!r}")
        values[key] = val
    return root_name, values


def is_valid_image_magic(path: Path) -> bool:
    header = path.read_bytes()[:12]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if header.startswith(b"\xff\xd8\xff"):
        return True
    if header.startswith((b"GIF87a", b"GIF89a")):
        return True
    return False


def is_inside_directory(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def generate_workshop_vdf(
    item_id: str,
    content_folder: Path,
    vdf_out: Path,
    changelog: str,
    tag: str,
    release_url: str,
    dry_run: bool,
    update_preview: bool = False,
    preview_file: Path | None = None,
    staging_dir: Path | None = None,
) -> dict[str, str]:
    """Generate and validate a minimal Valve KeyValues workshop.vdf outside contentfolder."""
    cleaned_id = item_id.strip()
    if cleaned_id == "":
        if not dry_run:
            raise ValueError(
                "STEAM_WORKSHOP_ITEM_ID is blank. Set a nonzero Workshop item ID before live publishing "
                "(CI never uses publishedfileid=0 to prevent duplicate listings)."
            )
        effective_id = "DRY_RUN_UNSET_ITEM_ID"
        workshop_url = ""
        print("::warning::STEAM_WORKSHOP_ITEM_ID is unset; using dry-run placeholder in VDF preview.")
    else:
        if not POSITIVE_INT_RE.match(cleaned_id):
            raise ValueError(
                f"Invalid STEAM_WORKSHOP_ITEM_ID ({cleaned_id!r}): must be a positive integer "
                "(0 and non-numeric values are rejected to prevent duplicate listings)."
            )
        effective_id = cleaned_id
        workshop_url = f"https://steamcommunity.com/sharedfiles/filedetails/?id={effective_id}"

    abs_content = content_folder.resolve()
    if not abs_content.is_dir():
        raise ValueError(f"Workshop contentfolder does not exist: {abs_content}")

    abs_vdf = vdf_out.resolve()
    if is_inside_directory(abs_vdf, abs_content):
        raise ValueError(f"workshop.vdf ({abs_vdf}) must be outside contentfolder ({abs_content}).")

    staged_preview: Path | None = None
    if update_preview:
        if preview_file is None or not preview_file.is_file():
            raise ValueError(f"update_preview is enabled, but preview_file does not exist: {preview_file}")
        ext = preview_file.suffix.lower()
        if ext not in ALLOWED_PREVIEW_EXTS:
            raise ValueError(
                f"Unsupported preview image extension {ext!r}; allowed: {sorted(ALLOWED_PREVIEW_EXTS)}"
            )
        size = preview_file.stat().st_size
        if size <= 0 or size > MAX_PREVIEW_BYTES:
            raise ValueError(
                f"Preview image size ({size} bytes) must be between 1 and {MAX_PREVIEW_BYTES} bytes (1 MiB)."
            )
        if not is_valid_image_magic(preview_file):
            raise ValueError(f"Preview file {preview_file} does not have valid PNG/JPEG/GIF header bytes.")

        preview_parent = (staging_dir or abs_vdf.parent).resolve()
        staged_preview = (preview_parent / f"preview{ext}").resolve()
        if is_inside_directory(staged_preview, abs_content):
            raise ValueError(f"Staged preview image ({staged_preview}) must be outside contentfolder ({abs_content}).")
        staged_preview.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(preview_file, staged_preview)

    note = format_changenote(changelog, tag, release_url)
    fields: list[tuple[str, str]] = [
        ("appid", KENSHI_APP_ID),
        ("publishedfileid", effective_id),
        ("contentfolder", str(abs_content)),
    ]
    if staged_preview is not None:
        fields.append(("previewfile", str(staged_preview)))
    fields.append(("changenote", note))

    lines = ['"workshopitem"', "{"]
    for key, val in fields:
        lines.append(f'\t"{key}"\t"{escape_vdf_value(val)}"')
    lines.append("}")
    vdf_text = "\n".join(lines) + "\n"

    abs_vdf.parent.mkdir(parents=True, exist_ok=True)
    abs_vdf.write_text(vdf_text, encoding="utf-8")

    # Parse back and verify structural invariants.
    root_name, parsed = parse_simple_vdf(abs_vdf.read_text(encoding="utf-8"))
    if root_name != "workshopitem":
        raise ValueError(f"Unexpected VDF root key: {root_name!r}")
    if parsed.get("appid") != KENSHI_APP_ID:
        raise ValueError(f"Unexpected VDF appid: {parsed.get('appid')!r}")
    if parsed.get("publishedfileid") != effective_id:
        raise ValueError("Generated VDF publishedfileid mismatch.")
    if not dry_run and not POSITIVE_INT_RE.match(parsed["publishedfileid"]):
        raise ValueError("Live VDF publishedfileid is not a positive integer.")
    if not os.path.isabs(parsed.get("contentfolder", "")):
        raise ValueError("Generated VDF contentfolder is not an absolute path.")
    for forbidden_key in ("title", "description", "visibility"):
        if forbidden_key in parsed:
            raise ValueError(f"Routine upload VDF must not set {forbidden_key!r}.")
    if not update_preview and "previewfile" in parsed:
        raise ValueError("Routine upload VDF must not set 'previewfile' when update_preview is false.")

    print(f"Generated and validated Workshop VDF: {abs_vdf}")
    print(f"  appid={KENSHI_APP_ID} publishedfileid={effective_id} contentfolder={abs_content}")
    print(f"  previewfile={staged_preview if staged_preview else '<omitted (preserved manually)>'}")

    return {
        "vdf_path": str(abs_vdf),
        "item_id": effective_id,
        "workshop_url": workshop_url,
        "preview_path": str(staged_preview) if staged_preview else "",
    }


def cmd_resolve(args: argparse.Namespace) -> int:
    try:
        info = resolve_release(
            repo=args.repo,
            release_tag=args.release_tag,
            git_dir=Path(args.git_dir),
            require_contents_sha=args.require_contents_sha,
        )
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    if args.release_json_out:
        Path(args.release_json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.release_json_out).write_text(json.dumps(info["release"], indent=2), encoding="utf-8")
    if args.changelog_out:
        Path(args.changelog_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.changelog_out).write_text(str(info["changelog"]), encoding="utf-8")

    print(f"Resolved release tag: {info['tag_name']}")
    print(f"Release URL: {info['release_url']}")
    print(f"Source commit: {info['commit_sha']}")
    assets_list = [str(info["zip_name"]), str(info["zip_sha_name"])]
    if info["contents_sha_name"]:
        assets_list.append(str(info["contents_sha_name"]))
    print(f"Required release assets verified: {', '.join(assets_list)}")

    write_github_output(
        args.github_output,
        {
            "tag_name": str(info["tag_name"]),
            "release_url": str(info["release_url"]),
            "commit_sha": str(info["commit_sha"]),
            "version": str(info["version"]),
            "zip_name": str(info["zip_name"]),
            "zip_sha_name": str(info["zip_sha_name"]),
            "contents_sha_name": str(info["contents_sha_name"]),
            "changelog": str(info["changelog"]),
        },
    )
    return 0


def cmd_verify_zip(args: argparse.Namespace) -> int:
    try:
        result = verify_zip_artifact(
            zip_path=Path(args.zip),
            zip_sha_path=Path(args.zip_sha),
            expected_tag=args.expected_tag,
        )
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    write_github_output(args.github_output, result)
    return 0


def cmd_stage_steam(args: argparse.Namespace) -> int:
    try:
        result = stage_and_verify_steam_content(
            zip_path=Path(args.zip),
            zip_sha256=args.zip_sha256,
            contents_sha_path=Path(args.contents_sha),
            staging_dir=Path(args.staging_dir),
        )
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    write_github_output(args.github_output, result)
    return 0


def cmd_generate_vdf(args: argparse.Namespace) -> int:
    try:
        changelog = Path(args.changelog_file).read_text(encoding="utf-8") if args.changelog_file else ""
        preview = Path(args.preview_file) if args.preview_file else None
        staging = Path(args.staging_dir) if args.staging_dir else None
        result = generate_workshop_vdf(
            item_id=args.item_id,
            content_folder=Path(args.content_folder),
            vdf_out=Path(args.vdf_out),
            changelog=changelog,
            tag=args.tag,
            release_url=args.release_url,
            dry_run=args.dry_run,
            update_preview=args.update_preview,
            preview_file=preview,
            staging_dir=staging,
        )
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    write_github_output(args.github_output, result)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standing Order Job release verification and staging CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_res = sub.add_parser("resolve", help="Resolve latest or exact Git tag to a published GitHub Release")
    p_res.add_argument("--repo", required=True)
    p_res.add_argument("--release-tag", default="latest")
    p_res.add_argument("--git-dir", default=".")
    p_res.add_argument("--require-contents-sha", action="store_true")
    p_res.add_argument("--release-json-out", default="")
    p_res.add_argument("--changelog-out", default="")
    p_res.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    p_res.set_defaults(func=cmd_resolve)

    p_ver = sub.add_parser("verify-zip", help="Verify downloaded ZIP SHA-256, safe entries, and meitou.json version")
    p_ver.add_argument("--zip", required=True)
    p_ver.add_argument("--zip-sha", required=True)
    p_ver.add_argument("--expected-tag", required=True)
    p_ver.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    p_ver.set_defaults(func=cmd_verify_zip)

    p_stg = sub.add_parser("stage-steam", help="Extract verified ZIP and verify extracted files against .contents.sha256")
    p_stg.add_argument("--zip", required=True)
    p_stg.add_argument("--zip-sha256", required=True)
    p_stg.add_argument("--contents-sha", required=True)
    p_stg.add_argument("--staging-dir", required=True)
    p_stg.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    p_stg.set_defaults(func=cmd_stage_steam)

    p_vdf = sub.add_parser("generate-vdf", help="Generate and validate minimal Steam Workshop VDF")
    p_vdf.add_argument("--item-id", default="")
    p_vdf.add_argument("--content-folder", required=True)
    p_vdf.add_argument("--vdf-out", required=True)
    p_vdf.add_argument("--changelog-file", default="")
    p_vdf.add_argument("--tag", required=True)
    p_vdf.add_argument("--release-url", required=True)
    p_vdf.add_argument("--dry-run", action="store_true")
    p_vdf.add_argument("--update-preview", action="store_true")
    p_vdf.add_argument("--preview-file", default="")
    p_vdf.add_argument("--staging-dir", default="")
    p_vdf.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    p_vdf.set_defaults(func=cmd_generate_vdf)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
