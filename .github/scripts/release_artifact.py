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
import urllib.error
import urllib.parse
import urllib.request
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
MAX_NEXUS_CHANGELOG_CHARS = 65535
MAX_DESCRIPTION_CHARS = 8000  # Steam Workshop ISteamUGC::SetItemDescription cap
STEAM_PUBLISHED_FILE_DETAILS_URL = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
STEAM_API_TIMEOUT_SECONDS = 15
VALID_UPDATE_DESCRIPTION_MODES: frozenset[str] = frozenset({"auto", "force", "skip"})
SEMVER_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
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
    nexus_changelog = format_nexus_changelog(
        changelog=changelog,
        tag=tag_name,
        release_url=release_url,
        commit_sha=commit_sha,
        repo=repo,
    )

    return {
        "tag_name": tag_name,
        "release_url": release_url,
        "commit_sha": commit_sha,
        "version": version,
        "zip_name": zip_name,
        "zip_sha_name": zip_sha_name,
        "contents_sha_name": contents_sha_name if contents_sha_name in asset_names else "",
        "changelog": changelog,
        "nexus_changelog": nexus_changelog,
        "release": release,
    }


def verify_zip_artifact(
    zip_path: Path,
    zip_sha_path: Path,
    expected_tag: str,
    release_url: str = "",
) -> dict[str, str]:
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
    effective_release_url = release_url.strip() or REPO_URL
    description = format_release_summary_line(expected_tag, game_version, effective_release_url)
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
    """Prepare a string value for SteamCMD Valve KeyValues double-quoted syntax.

    SteamCMD's workshop_build_item VDF reader preserves real newlines inside quotes
    and does not unescape C-style \\n or \\" sequences, so literal newlines are kept
    and embedded double quotes are replaced with single quotes.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace('"', "'")


def _decode_single_quoted_yaml(chunks: list[str]) -> str:
    out_parts: list[str] = []
    blank_run = 0
    for raw in chunks:
        part = raw.replace("''", "'")
        if part == "":
            blank_run += 1
        else:
            if out_parts:
                out_parts.append("\n" * blank_run if blank_run > 0 else " ")
            blank_run = 0
            out_parts.append(part)
    return "".join(out_parts).strip()


def _fold_yaml_lines(lines: list[str]) -> str:
    out: list[str] = []
    for line in lines:
        if line == "":
            out.append("\n")
        elif out and not out[-1].endswith("\n"):
            out.append(" " + line)
        else:
            out.append(line)
    return "".join(out)


def parse_simple_yaml(text: str) -> dict[str, str]:
    """Parse flat Copier answer YAML (plain, quoted, and block scalars) using stdlib only."""
    lines = text.splitlines()
    data: dict[str, str] = {}
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        match = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if not match:
            i += 1
            continue

        key, rest = match.group(1), match.group(2)
        i += 1

        if rest in ("|", "|-", ">", ">-"):
            block_lines: list[str] = []
            indent: int | None = None
            while i < n:
                nxt = lines[i]
                if nxt.strip() == "":
                    block_lines.append("")
                    i += 1
                    continue
                cur_indent = len(nxt) - len(nxt.lstrip(" "))
                if cur_indent == 0 and re.match(r"^[A-Za-z0-9_]+:", nxt):
                    break
                if indent is None:
                    indent = cur_indent
                block_lines.append(nxt[indent:] if len(nxt) >= indent else nxt.lstrip(" "))
                i += 1
            val = _fold_yaml_lines(block_lines) if rest.startswith(">") else "\n".join(block_lines)
            data[key] = val.strip() if rest.endswith("-") else val
        elif rest.startswith("'") and (len(rest) == 1 or not rest.endswith("'") or rest == "'"):
            chunks = [rest[1:]]
            while i < n:
                nxt = lines[i]
                i += 1
                stripped_nxt = nxt.lstrip(" ")
                trailing = len(stripped_nxt) - len(stripped_nxt.rstrip("'"))
                if trailing % 2 == 1:
                    chunks.append(stripped_nxt[:-1])
                    break
                chunks.append(stripped_nxt)
            data[key] = _decode_single_quoted_yaml(chunks)
        elif rest.startswith("'") and rest.endswith("'") and len(rest) >= 2:
            data[key] = rest[1:-1].replace("''", "'")
        elif rest.startswith('"') and rest.endswith('"') and len(rest) >= 2:
            data[key] = rest[1:-1].replace('\\"', '"').replace("\\n", "\n")
        else:
            data[key] = rest.strip()

    return data


def _eval_store_expr(expr: str, ctx: dict[str, str]) -> object:
    """Evaluate the constrained Jinja expressions used by docs/store/steam.md.jinja."""
    s = expr.strip()

    # Handle trailing .startswith('#') check
    sw_match = re.fullmatch(r"\((.+)\)\.startswith\((['\"])(.*?)\2\)", s)
    if sw_match:
        inner = str(_eval_store_expr(sw_match.group(1), ctx))
        return inner.startswith(sw_match.group(3))

    # Handle trailing .rstrip('/')
    rs_match = re.fullmatch(r"\((.+)\)\.rstrip\((['\"])(.*?)\2\)", s)
    if rs_match:
        inner = str(_eval_store_expr(rs_match.group(1), ctx))
        return inner.rstrip(rs_match.group(3))

    # Split top-level pipe filters (ignoring pipes inside parentheses/quotes)
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "|" and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf).strip())

    base = parts[0]
    if (base.startswith("'") and base.endswith("'")) or (base.startswith('"') and base.endswith('"')):
        val: object = base[1:-1]
    elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", base):
        val = ctx.get(base, "")
    else:
        raise ValueError(f"Unsupported store template expression: {expr!r}")

    for flt in parts[1:]:
        if flt == "trim":
            val = str(val).strip()
        elif flt.startswith("default(") and flt.endswith(")"):
            fallback_expr = flt[len("default(") : -1].strip()
            if not val:
                val = _eval_store_expr(fallback_expr, ctx)
        else:
            raise ValueError(f"Unsupported store template filter: {flt!r}")

    return val


def render_store_template(template_text: str, answers: dict[str, str]) -> str:
    """Render docs/store/steam.md.jinja using Jinja2 if installed, else pure-stdlib evaluator."""
    try:
        import jinja2  # type: ignore[import-not-found]
    except ImportError:
        jinja2 = None

    if jinja2 is not None:
        env = jinja2.Environment(autoescape=False, undefined=jinja2.Undefined)
        rendered = env.from_string(template_text).render(**answers).strip()
    else:
        # Strip Jinja comments {# ... #}
        text = re.sub(r"\{#.*?#\}", "", template_text, flags=re.DOTALL)

        # Tokenize {% if ... %}, {% endif %}, and {{ ... }} (handling leading '-' whitespace trim).
        token_re = re.compile(r"(\{%-?\s*.*?\s*-?%\}|\{\{-?\s*.*?\s*-?\}\})", re.DOTALL)
        tokens = token_re.split(text)

        out: list[str] = []
        cond_stack: list[bool] = [True]
        trim_next = False

        for tok in tokens:
            if not tok:
                continue
            if tok.startswith("{%"):
                trim_prev = tok.startswith("{%-")
                trim_cur_next = tok.endswith("-%}")
                inner = tok[3 if trim_prev else 2 : -3 if trim_cur_next else -2].strip()
                if trim_prev and out:
                    out[-1] = out[-1].rstrip(" \t\r\n")
                if inner.startswith("if "):
                    cond_expr = inner[3:].strip()
                    parent_active = cond_stack[-1]
                    cond_stack.append(bool(_eval_store_expr(cond_expr, answers)) if parent_active else False)
                elif inner == "endif":
                    if len(cond_stack) <= 1:
                        raise ValueError("Unmatched {% endif %} in store template.")
                    cond_stack.pop()
                else:
                    raise ValueError(f"Unsupported control tag in store template: {inner!r}")
                trim_next = trim_cur_next
            elif tok.startswith("{{"):
                trim_prev = tok.startswith("{{-")
                trim_cur_next = tok.endswith("-}}")
                inner = tok[3 if trim_prev else 2 : -3 if trim_cur_next else -2].strip()
                if trim_prev and out:
                    out[-1] = out[-1].rstrip(" \t\r\n")
                if cond_stack[-1]:
                    out.append(str(_eval_store_expr(inner, answers)))
                trim_next = trim_cur_next
            else:
                chunk = tok.lstrip(" \t\r\n") if trim_next else tok
                trim_next = False
                if cond_stack[-1]:
                    out.append(chunk)

        if len(cond_stack) != 1:
            raise ValueError("Unclosed {% if %} block in store template.")
        rendered = "".join(out).strip()

    if "{{" in rendered or "{%" in rendered:
        raise ValueError("Rendered store description still contains unexpanded Jinja tags.")
    return rendered


def _convert_inline_md_to_bbcode(text: str) -> str:
    """Convert inline Markdown links, bold, and backticks into Steam Workshop BBCode."""
    # Convert Markdown links [label](url) first so bold around links becomes [b][url=...]...[/url][/b].
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"[url=\2]\1[/url]", text)
    # Convert **bold** -> [b]bold[/b]
    text = re.sub(r"\*\*([^*]+)\*\*", r"[b]\1[/b]", text)
    # Steam's [code] is block-level; strip inline backticks cleanly instead of breaking paragraphs.
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def markdown_to_steam_bbcode(md_text: str) -> str:
    """Convert store-page Markdown into Steam Workshop BBCode."""
    lines = md_text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    out: list[str] = []
    list_kind: str | None = None  # "list" or "olist"

    def close_list() -> None:
        nonlocal list_kind
        if list_kind is not None:
            out.append(f"[/{list_kind}]")
            list_kind = None

    def last_is_heading() -> bool:
        return bool(out) and bool(re.match(r"^\[h[1-3]\].*\[/h[1-3]\]$", out[-1]))

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            close_list()
            if out and out[-1] != "" and not last_is_heading():
                out.append("")
            continue

        h_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if h_match:
            close_list()
            if out and out[-1] != "" and not last_is_heading():
                out.append("")
            level = len(h_match.group(1))
            heading = _convert_inline_md_to_bbcode(h_match.group(2).strip())
            out.append(f"[h{level}]{heading}[/h{level}]")
            continue

        ul_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if ul_match:
            if list_kind != "list":
                close_list()
                if out and out[-1] == "" and len(out) >= 2 and re.match(r"^\[h[1-3]\].*\[/h[1-3]\]$", out[-2]):
                    out.pop()
                out.append("[list]")
                list_kind = "list"
            out.append(f"[*] {_convert_inline_md_to_bbcode(ul_match.group(1).strip())}")
            continue

        ol_match = re.match(r"^\d+\.\s+(.+)$", stripped)
        if ol_match:
            if list_kind != "olist":
                close_list()
                if out and out[-1] == "" and len(out) >= 2 and re.match(r"^\[h[1-3]\].*\[/h[1-3]\]$", out[-2]):
                    out.pop()
                out.append("[olist]")
                list_kind = "olist"
            out.append(f"[*] {_convert_inline_md_to_bbcode(ol_match.group(1).strip())}")
            continue

        close_list()
        out.append(_convert_inline_md_to_bbcode(stripped))

    close_list()
    return "\n".join(out).strip()


def load_steam_description(
    description_file: Path | None = None,
    steam_template: Path | None = None,
    copier_answers: Path | None = None,
) -> str:
    """Load and convert Steam Workshop description to BBCode from a rendered file or Jinja template."""
    if description_file is not None:
        if not description_file.is_file():
            raise ValueError(f"Steam description file does not exist: {description_file}")
        raw_text = description_file.read_text(encoding="utf-8").strip()
        if not raw_text:
            raise ValueError(f"Steam description file is empty: {description_file}")
        return markdown_to_steam_bbcode(raw_text) if description_file.suffix.lower() == ".md" else raw_text

    if steam_template is not None:
        if not steam_template.is_file():
            raise ValueError(f"Steam store template does not exist: {steam_template}")
        if copier_answers is None or not copier_answers.is_file():
            raise ValueError(f"Copier answers file required for steam_template does not exist: {copier_answers}")
        answers = parse_simple_yaml(copier_answers.read_text(encoding="utf-8"))
        rendered_md = render_store_template(steam_template.read_text(encoding="utf-8"), answers)
        if not rendered_md:
            raise ValueError(f"Rendered Steam store template is empty: {steam_template}")
        return markdown_to_steam_bbcode(rendered_md)

    return ""


def _normalize_steam_description(text: str) -> str:
    """Normalize a Steam BBCode description for idempotent equality comparison."""
    escaped = escape_vdf_value(text)
    lines = [line.rstrip() for line in escaped.splitlines()]
    return "\n".join(lines).strip()


def fetch_live_steam_description(
    item_id: str,
    timeout: int = STEAM_API_TIMEOUT_SECONDS,
) -> tuple[str | None, str]:
    """Fetch the live public Steam Workshop item description via Valve's unauthenticated Web API.

    Returns (description, status_note). Returns (None, reason) when the item is private/hidden,
    awaiting moderation, or unreachable so callers can fall back to git-tag diffing.
    """
    cleaned_id = item_id.strip()
    if not POSITIVE_INT_RE.match(cleaned_id):
        return None, "item ID unset"

    body = urllib.parse.urlencode({"itemcount": "1", "publishedfileids[0]": cleaned_id}).encode("utf-8")
    req = urllib.request.Request(
        STEAM_PUBLISHED_FILE_DETAILS_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "StandingOrderJob-CI/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return None, f"Steam API unavailable ({type(exc).__name__})"

    details = payload.get("response", {}).get("publishedfiledetails", [])
    if not isinstance(details, list) or not details or not isinstance(details[0], dict):
        return None, "Steam API returned no item details"

    item = details[0]
    result_code = item.get("result")
    live_desc = item.get("description")
    if result_code != 1 or not isinstance(live_desc, str):
        return None, f"live item not publicly readable (result={result_code})"
    return live_desc, "ok"


def _semver_tag_key(tag: str) -> tuple[int, int, int, str] | None:
    match = SEMVER_TAG_RE.match(tag.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), tag.strip())


def find_previous_release_tag(git_dir: Path, current_tag: str) -> str | None:
    """Return the highest SemVer tag strictly below current_tag in git_dir, or None."""
    cur_key = _semver_tag_key(current_tag)
    if cur_key is None:
        return None
    result = subprocess.run(
        ["git", "-C", str(git_dir), "tag", "--list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    candidates: list[tuple[tuple[int, int, int, str], str]] = []
    for raw in result.stdout.splitlines():
        tag = raw.strip()
        if not tag or tag == current_tag.strip():
            continue
        key = _semver_tag_key(tag)
        if key is not None and key[:3] < cur_key[:3]:
            candidates.append((key, tag))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[-1][1]


def _git_show_text(git_dir: Path, ref: str, rel_path: str) -> str | None:
    res = subprocess.run(
        ["git", "-C", str(git_dir), "show", f"{ref}:{rel_path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return res.stdout if res.returncode == 0 else None


def load_steam_description_at_git_ref(
    git_dir: Path,
    ref: str,
    steam_template_rel: str,
    copier_answers_rel: str,
) -> str | None:
    """Render Steam BBCode from docs/store/steam.md.jinja + .copier-answers.yml at a git ref."""
    tpl_text = _git_show_text(git_dir, ref, steam_template_rel)
    answers_text = _git_show_text(git_dir, ref, copier_answers_rel)
    if not tpl_text or not answers_text:
        return None
    answers = parse_simple_yaml(answers_text)
    rendered_md = render_store_template(tpl_text, answers)
    if not rendered_md:
        return None
    return markdown_to_steam_bbcode(rendered_md)


def resolve_steam_description_update(
    mode: str,
    item_id: str,
    tag: str,
    description_file: Path | None = None,
    steam_template: Path | None = None,
    copier_answers: Path | None = None,
    git_dir: Path | None = None,
    live_fetcher=fetch_live_steam_description,
) -> tuple[str, str]:
    """Decide whether to include 'description' in workshop.vdf based on auto/force/skip mode.

    Returns (description_to_write, status_label). Empty description_to_write omits the VDF key.
    """
    cleaned_mode = (mode or "auto").strip().lower()
    if cleaned_mode not in VALID_UPDATE_DESCRIPTION_MODES:
        raise ValueError(
            f"Invalid update_description mode {mode!r}; expected one of {sorted(VALID_UPDATE_DESCRIPTION_MODES)}."
        )
    if cleaned_mode == "skip":
        return "", "omitted (update_description=skip)"

    candidate = load_steam_description(
        description_file=description_file,
        steam_template=steam_template,
        copier_answers=copier_answers,
    )
    if not candidate:
        return "", "omitted (no description source configured)"

    if cleaned_mode == "force":
        return candidate, f"updated (forced, {len(candidate)} chars BBCode)"

    # Auto mode: first compare against live public Steam Workshop description.
    norm_candidate = _normalize_steam_description(candidate)
    live_desc, live_note = live_fetcher(item_id)
    if live_desc is not None:
        if _normalize_steam_description(live_desc) == norm_candidate:
            return "", "unchanged vs live Steam Workshop (omitted)"
        return candidate, f"updated (changed vs live Steam Workshop, {len(candidate)} chars BBCode)"

    # Fallback when item is private/hidden/moderated or live API is unavailable: diff vs previous git tag.
    if git_dir is not None and steam_template is not None and copier_answers is not None and tag.strip():
        prev_tag = find_previous_release_tag(git_dir, tag)
        if prev_tag:
            repo_root = git_dir.resolve()
            try:
                tpl_rel = steam_template.resolve().relative_to(repo_root).as_posix()
                ans_rel = copier_answers.resolve().relative_to(repo_root).as_posix()
            except ValueError:
                tpl_rel = steam_template.as_posix()
                ans_rel = copier_answers.as_posix()
            prev_bbcode = load_steam_description_at_git_ref(git_dir, prev_tag, tpl_rel, ans_rel)
            if prev_bbcode is not None:
                if _normalize_steam_description(prev_bbcode) == norm_candidate:
                    return "", f"unchanged vs git tag {prev_tag} (omitted; {live_note})"
                return candidate, f"updated (changed vs git tag {prev_tag}, {len(candidate)} chars BBCode; {live_note})"

    return candidate, f"updated (no baseline to diff, {len(candidate)} chars BBCode; {live_note})"


def build_release_note_url(release_url: str, tag: str) -> str:
    """Append /releases/tag/<tag> to a GitHub repo URL when a tag is present; fall back cleanly when absent."""
    base = release_url.strip().rstrip("/")
    clean_tag = tag.strip()
    if not base or not clean_tag:
        return base
    if "/releases/tag/" in base:
        return base
    if re.fullmatch(r"https?://github\.com/[^/]+/[^/]+/releases", base):
        return f"{base}/tag/{clean_tag}"
    if re.fullmatch(r"https?://github\.com/[^/]+/[^/]+", base):
        return f"{base}/releases/tag/{clean_tag}"
    return base


def _strip_inline_md_for_plain_text(text: str) -> str:
    """Convert inline Markdown to clean plain text suitable for Nexus Mods bullet lines."""
    # Drop trailing release-please commit links such as " ([8467d56](https://github.com/.../commit/...))".
    text = re.sub(r"\s*\(\[[0-9a-fA-F]{7,40}\]\(https?://[^)\s]+\)\)", "", text)
    # Collapse Markdown issue/PR links "[#12](https://...)" -> "#12".
    text = re.sub(r"\[(#\d+)\]\(https?://[^)\s]+\)", r"\1", text)
    # Convert remaining Markdown links "[label](url)" -> "label" (or "url" when label matches).
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda m: m.group(2) if m.group(1).strip() == m.group(2).strip() else m.group(1).strip(),
        text,
    )
    # Strip bold and inline code formatting.
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_plain_changelog_bullets(changelog: str) -> tuple[list[str], str]:
    """Extract clean plain-text change bullets and optional CI run URL from GitHub Release notes."""
    raw = changelog.replace("\r\n", "\n").replace("\r", "\n").strip()

    # Capture originating CI run URL before stripping the provenance/checksums tail.
    ci_run_match = re.search(r"https?://github\.com/[^\s)]+/actions/runs/\d+", raw)
    ci_run_url = ci_run_match.group(0) if ci_run_match else ""

    notes_body = raw
    for marker in ("### Build provenance & checksums", "### Package checksums (SHA-256)"):
        idx = notes_body.find(marker)
        if idx != -1:
            notes_body = notes_body[:idx]
            break

    bullets: list[str] = []
    current_section = ""
    for line in notes_body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip horizontal rules and Markdown tables.
        if re.fullmatch(r"[-*_]{3,}", stripped) or stripped.startswith("|"):
            continue
        # Skip top-level release title headings ("# ..." or "## [0.1.1](...)").
        if re.match(r"^#{1,2}\s+", stripped):
            continue
        # Track section headings ("### Features", "### Bug Fixes", etc.) as prefixes.
        sec_match = re.match(r"^#{3,6}\s+(.+)$", stripped)
        if sec_match:
            current_section = _strip_inline_md_for_plain_text(sec_match.group(1))
            continue

        item = re.sub(r"^(?:[-*+•]\s+|\d+\.\s+)", "", stripped)
        cleaned = _strip_inline_md_for_plain_text(item)
        if not cleaned:
            continue
        if current_section and not cleaned.lower().startswith(f"{current_section.lower()}:"):
            bullets.append(f"{current_section}: {cleaned}")
        else:
            bullets.append(cleaned)

    return bullets, ci_run_url


def format_nexus_changelog(
    changelog: str,
    tag: str,
    release_url: str,
    commit_sha: str = "",
    repo: str = "",
    max_chars: int = MAX_NEXUS_CHANGELOG_CHARS,
) -> str:
    """Format GitHub Release notes into plain-text one-bullet-per-line entries for Nexus Mods.

    Nexus Mods renders every non-empty line in a version changelog as its own bullet point
    without Markdown/table support, so headings, tables, and bullet markers are stripped.
    """
    bullets, ci_run_url = _extract_plain_changelog_bullets(changelog)

    clean_tag = tag.strip()
    if not bullets:
        bullets.append(f"Release {clean_tag}" if clean_tag else "Release")

    target_url = build_release_note_url(release_url, clean_tag)
    if target_url:
        bullets.append(f"GitHub Release (checksums & attestations): {target_url}")
    clean_sha = commit_sha.strip()
    clean_repo = repo.strip().strip("/")
    if clean_sha and clean_repo:
        bullets.append(f"Source commit: https://github.com/{clean_repo}/commit/{clean_sha}")
    if ci_run_url:
        bullets.append(f"Originating CI run: {ci_run_url}")

    # Deduplicate while preserving order.
    deduped: list[str] = []
    seen: set[str] = set()
    for entry in bullets:
        if entry not in seen:
            seen.add(entry)
            deduped.append(entry)

    result = "\n".join(deduped)
    if len(result) > max_chars:
        suffix = f"\nFull changelog: {target_url}" if target_url else "\n..."
        keep = max(0, max_chars - len(suffix))
        result = result[:keep].rstrip() + suffix
    return result


def format_release_summary_line(tag: str, game_version: str = "", release_url: str = "") -> str:
    """Format 'Release <tag> - MeitouClient <game_version> - <release_url>' for Nexus descriptions and Steam change notes."""
    clean_tag = tag.strip()
    if clean_tag and not clean_tag.startswith("v"):
        clean_tag = f"v{clean_tag}"
    clean_game_version = game_version.strip()
    target_url = build_release_note_url(release_url, clean_tag) if release_url else ""

    parts: list[str] = [f"Release {clean_tag}" if clean_tag else "Release"]
    if clean_game_version:
        parts.append(f"MeitouClient {clean_game_version}")
    if target_url:
        parts.append(target_url)
    return " - ".join(parts)


def format_changenote(
    changelog: str,
    tag: str,
    release_url: str,
    game_version: str = "",
    max_chars: int = MAX_CHANGENOTE_CHARS,
) -> str:
    """Format GitHub Release notes into concise, link-safe Steam Workshop change notes."""
    header = format_release_summary_line(tag, game_version, release_url)
    bullets, _ci_run_url = _extract_plain_changelog_bullets(changelog)

    # Strip any parenthesized URLs "(https://...)" so Steam's malicious link filter never triggers.
    clean_bullets: list[str] = []
    for entry in bullets:
        sanitized = re.sub(r"\((https?://[^)\s]+)\)", r"\1", entry).strip()
        if sanitized:
            clean_bullets.append(f"- {sanitized}")

    if not clean_bullets:
        note = header
    else:
        body = "\n".join(clean_bullets)
        note = f"{header}\n\n{body}"

    if len(note) > max_chars:
        suffix = "\n..."
        keep = max(0, max_chars - len(suffix))
        note = note[:keep].rstrip() + suffix
    return note


def parse_simple_vdf(vdf_text: str) -> tuple[str, dict[str, str]]:
    """Parse our flat generated Valve KeyValues workshopitem block (supporting multiline quoted values)."""
    text = vdf_text
    n = len(text)
    pos = 0

    def skip_ws() -> None:
        nonlocal pos
        while pos < n and text[pos] in " \t\r\n":
            pos += 1

    def read_quoted(allow_newlines: bool) -> str:
        nonlocal pos
        if pos >= n or text[pos] != '"':
            raise ValueError(f"Generated VDF expected quoted string at offset {pos}.")
        pos += 1
        start = pos
        while pos < n and text[pos] != '"':
            if not allow_newlines and text[pos] in "\r\n":
                raise ValueError("Generated VDF key contains a newline.")
            pos += 1
        if pos >= n:
            raise ValueError("Generated VDF has an unterminated quoted string.")
        val = text[start:pos]
        pos += 1
        return val

    skip_ws()
    root_name = read_quoted(allow_newlines=False)
    skip_ws()
    if pos >= n or text[pos] != "{":
        raise ValueError("Generated VDF does not have expected root { ... } structure.")
    pos += 1

    values: dict[str, str] = {}
    while True:
        skip_ws()
        if pos >= n:
            raise ValueError("Generated VDF is missing closing '}'.")
        if text[pos] == "}":
            pos += 1
            break
        key = read_quoted(allow_newlines=False)
        skip_ws()
        val = read_quoted(allow_newlines=True)
        if key in values:
            raise ValueError(f"Generated VDF has duplicate key: {key!r}")
        values[key] = val

    skip_ws()
    if pos != n:
        raise ValueError(f"Generated VDF has unexpected trailing content at offset {pos}.")
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
    description: str = "",
    game_version: str = "",
) -> dict[str, str]:
    """Generate and validate a Valve KeyValues workshop.vdf outside contentfolder."""
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

    cleaned_description = description.strip()
    if len(cleaned_description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(
            f"Steam Workshop description length ({len(cleaned_description)} chars) exceeds "
            f"Steam's {MAX_DESCRIPTION_CHARS}-character limit."
        )

    effective_game_version = game_version.strip()
    if not effective_game_version:
        meta_path = abs_content / "meitou.json"
        if meta_path.is_file():
            meta_payload = json.loads(meta_path.read_text(encoding="utf-8"))
            effective_game_version = str(meta_payload.get("gameVersion", "")).strip()

    note = format_changenote(changelog, tag, release_url, game_version=effective_game_version)
    fields: list[tuple[str, str]] = [
        ("appid", KENSHI_APP_ID),
        ("publishedfileid", effective_id),
        ("contentfolder", str(abs_content)),
    ]
    if staged_preview is not None:
        fields.append(("previewfile", str(staged_preview)))
    if cleaned_description:
        fields.append(("description", cleaned_description))
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
    for forbidden_key in ("title", "visibility"):
        if forbidden_key in parsed:
            raise ValueError(f"Routine upload VDF must not set {forbidden_key!r}.")
    if not update_preview and "previewfile" in parsed:
        raise ValueError("Routine upload VDF must not set 'previewfile' when update_preview is false.")
    if not cleaned_description and "description" in parsed:
        raise ValueError("Routine upload VDF must not set 'description' when no description is supplied.")

    print(f"Generated and validated Workshop VDF: {abs_vdf}")
    print(f"  appid={KENSHI_APP_ID} publishedfileid={effective_id} contentfolder={abs_content}")
    print(f"  previewfile={staged_preview if staged_preview else '<omitted (preserved manually)>'}")
    print(
        f"  description={f'included ({len(cleaned_description)} chars, Steam BBCode)' if cleaned_description else '<omitted>'}"
    )

    return {
        "vdf_path": str(abs_vdf),
        "item_id": effective_id,
        "workshop_url": workshop_url,
        "preview_path": str(staged_preview) if staged_preview else "",
        "description_chars": str(len(cleaned_description)),
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
            "nexus_changelog": str(info["nexus_changelog"]),
        },
    )
    return 0


def cmd_verify_zip(args: argparse.Namespace) -> int:
    try:
        result = verify_zip_artifact(
            zip_path=Path(args.zip),
            zip_sha_path=Path(args.zip_sha),
            expected_tag=args.expected_tag,
            release_url=args.release_url,
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
        description, description_status = resolve_steam_description_update(
            mode=args.update_description,
            item_id=args.item_id,
            tag=args.tag,
            description_file=Path(args.description_file) if args.description_file else None,
            steam_template=Path(args.steam_template) if args.steam_template else None,
            copier_answers=Path(args.copier_answers) if args.copier_answers else None,
            git_dir=Path(args.git_dir) if args.git_dir else None,
        )
        print(f"Steam Workshop description policy ({args.update_description}): {description_status}")
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
            description=description,
            game_version=args.game_version,
        )
        result["description_status"] = description_status
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
    p_ver.add_argument("--release-url", default="")
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
    p_vdf.add_argument("--tag", default="")
    p_vdf.add_argument("--release-url", required=True)
    p_vdf.add_argument("--game-version", default="")
    p_vdf.add_argument("--dry-run", action="store_true")
    p_vdf.add_argument("--update-preview", action="store_true")
    p_vdf.add_argument("--preview-file", default="")
    p_vdf.add_argument("--staging-dir", default="")
    p_vdf.add_argument("--description-file", default="")
    p_vdf.add_argument("--steam-template", default="")
    p_vdf.add_argument("--copier-answers", default="")
    p_vdf.add_argument("--update-description", default="auto", choices=sorted(VALID_UPDATE_DESCRIPTION_MODES))
    p_vdf.add_argument("--git-dir", default=".")
    p_vdf.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    p_vdf.set_defaults(func=cmd_generate_vdf)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
