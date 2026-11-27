#!/usr/bin/env python3
"""Unit tests for release_artifact.py and nexus_preflight.py."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import nexus_preflight  # noqa: E402
import release_artifact  # noqa: E402


def build_valid_release_artifacts(
    root: Path,
    version: str = "0.1.1",
    extra_entries: list[tuple[zipfile.ZipInfo, bytes]] | None = None,
) -> tuple[Path, Path, Path]:
    """Create a deterministic test ZIP plus .zip.sha256 and .contents.sha256 manifests."""
    zip_name = f"StandingOrderJob-{version}.zip"
    zip_path = root / zip_name
    zip_sha_path = root / f"{zip_name}.sha256"
    contents_sha_path = root / f"StandingOrderJob-{version}.contents.sha256"

    files = {
        "StandingOrderJob/StandingOrderJob.dll": b"FAKE-DLL-BYTES",
        "StandingOrderJob/StandingOrderJob.mod": b"FAKE-MOD-BYTES",
        "StandingOrderJob/meitou.json": json.dumps(
            {"id": "standing-order-job", "version": version, "gameVersion": ">=0.24.0"}
        ).encode("utf-8"),
    }

    sha_lines: list[str] = []
    with zipfile.ZipFile(zip_path, "w") as zf:
        for rel in sorted(files):
            data = files[rel]
            sha_lines.append(f"{hashlib.sha256(data).hexdigest()}  {rel}\n")
            info = zipfile.ZipInfo(rel)
            info.external_attr = (0o644 & 0xFFFF) << 16
            zf.writestr(info, data)
        for extra_info, extra_data in extra_entries or []:
            zf.writestr(extra_info, extra_data)

    contents_sha_path.write_text("".join(sha_lines), encoding="utf-8")
    zip_digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_sha_path.write_text(f"{zip_digest}  {zip_name}\n", encoding="utf-8")
    return zip_path, zip_sha_path, contents_sha_path


class ReleaseArtifactTests(unittest.TestCase):
    def test_resolve_release_latest_and_explicit_tag_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
            (repo_dir / "README.md").write_text("hi\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_dir, check=True)
            subprocess.run(["git", "tag", "v0.1.0"], cwd=repo_dir, check=True)
            subprocess.run(["git", "tag", "v0.1.1"], cwd=repo_dir, check=True)
            subprocess.run(["git", "tag", "v0.1.2-unreleased"], cwd=repo_dir, check=True)

            published_rel = {
                "tagName": "v0.1.1",
                "url": "https://github.com/org/repo/releases/tag/v0.1.1",
                "name": "v0.1.1",
                "body": "Release notes",
                "isDraft": False,
                "isPrerelease": False,
                "targetCommitish": "main",
                "assets": [
                    {"name": "StandingOrderJob-0.1.1.zip"},
                    {"name": "StandingOrderJob-0.1.1.zip.sha256"},
                    {"name": "StandingOrderJob-0.1.1.contents.sha256"},
                ],
            }

            with mock.patch.object(release_artifact, "fetch_github_release", return_value=(0, published_rel, "")):
                res_latest = release_artifact.resolve_release("org/repo", "latest", repo_dir, require_contents_sha=True)
                self.assertEqual(res_latest["tag_name"], "v0.1.1")
                res_blank = release_artifact.resolve_release("org/repo", "", repo_dir, require_contents_sha=True)
                self.assertEqual(res_blank["tag_name"], "v0.1.1")
                res_exact = release_artifact.resolve_release("org/repo", "v0.1.1", repo_dir, require_contents_sha=True)
                self.assertEqual(res_exact["version"], "0.1.1")

            # Missing Git tag must report exact error and never fall back to latest.
            with self.assertRaises(ValueError) as ctx_missing:
                release_artifact.resolve_release("org/repo", "v9.9.9", repo_dir, require_contents_sha=False)
            self.assertEqual(str(ctx_missing.exception), "Release tag not found: 'v9.9.9'")

            # Prefix of an existing tag must NOT match.
            with self.assertRaises(ValueError) as ctx_prefix:
                release_artifact.resolve_release("org/repo", "v0.1", repo_dir, require_contents_sha=False)
            self.assertEqual(str(ctx_prefix.exception), "Release tag not found: 'v0.1'")

            # Existing Git tag with no published release (or draft) must report distinct error.
            with mock.patch.object(release_artifact, "fetch_github_release", return_value=(1, None, "release not found")):
                with self.assertRaises(ValueError) as ctx_unpub:
                    release_artifact.resolve_release("org/repo", "v0.1.2-unreleased", repo_dir, require_contents_sha=False)
                self.assertEqual(
                    str(ctx_unpub.exception),
                    "Tag 'v0.1.2-unreleased' exists, but no published GitHub Release was found",
                )

            draft_rel = dict(published_rel, isDraft=True)
            with mock.patch.object(release_artifact, "fetch_github_release", return_value=(0, draft_rel, "")):
                with self.assertRaises(ValueError) as ctx_draft:
                    release_artifact.resolve_release("org/repo", "v0.1.1", repo_dir, require_contents_sha=False)
                self.assertEqual(
                    str(ctx_draft.exception),
                    "Tag 'v0.1.1' exists, but no published GitHub Release was found",
                )

            # Missing contents manifest must fail when require_contents_sha=True.
            no_contents_rel = dict(
                published_rel,
                assets=[
                    {"name": "StandingOrderJob-0.1.1.zip"},
                    {"name": "StandingOrderJob-0.1.1.zip.sha256"},
                ],
            )
            with mock.patch.object(release_artifact, "fetch_github_release", return_value=(0, no_contents_rel, "")):
                with self.assertRaises(ValueError) as ctx_asset:
                    release_artifact.resolve_release("org/repo", "v0.1.1", repo_dir, require_contents_sha=True)
                self.assertIn("StandingOrderJob-0.1.1.contents.sha256", str(ctx_asset.exception))

    def test_verify_zip_and_stage_steam_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path, zip_sha, contents_sha = build_valid_release_artifacts(root, version="0.1.1")

            with redirect_stdout(io.StringIO()):
                verified = release_artifact.verify_zip_artifact(zip_path, zip_sha, expected_tag="v0.1.1")
                self.assertEqual(verified["version"], "0.1.1")
                self.assertEqual(verified["zip_name"], "StandingOrderJob-0.1.1.zip")
                self.assertEqual(verified["description"], "MeitouClient code mod (>=0.24.0)")
                self.assertNotIn("provenance", verified["description"].lower())

                staged = release_artifact.stage_and_verify_steam_content(
                    zip_path=zip_path,
                    zip_sha256=verified["zip_sha256"],
                    contents_sha_path=contents_sha,
                    staging_dir=root / "workshop-staging",
                )

            content_folder = Path(staged["content_folder"])
            self.assertTrue((content_folder / "StandingOrderJob.dll").is_file())
            self.assertTrue((content_folder / "StandingOrderJob.mod").is_file())
            self.assertTrue((content_folder / "meitou.json").is_file())

    def test_verify_zip_rejects_bad_hash_traversal_symlinks_and_extras(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path, zip_sha, _ = build_valid_release_artifacts(root, version="0.1.1")
            zip_sha.write_text("0" * 64 + f"  {zip_path.name}\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    release_artifact.verify_zip_artifact(zip_path, zip_sha, expected_tag="v0.1.1")

        # Traversal entry
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = zipfile.ZipInfo("../escape.txt")
            zip_path, zip_sha, _ = build_valid_release_artifacts(root, extra_entries=[(bad, b"boom")])
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError) as ctx:
                    release_artifact.verify_zip_artifact(zip_path, zip_sha, expected_tag="v0.1.1")
            self.assertIn("traversal", str(ctx.exception).lower())

        # Symlink entry
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sym = zipfile.ZipInfo("StandingOrderJob/link.txt")
            sym.external_attr = (stat.S_IFLNK | 0o777) << 16
            zip_path, zip_sha, _ = build_valid_release_artifacts(root, extra_entries=[(sym, b"/etc/passwd")])
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError) as ctx:
                    release_artifact.verify_zip_artifact(zip_path, zip_sha, expected_tag="v0.1.1")
            self.assertIn("symlink", str(ctx.exception).lower())

        # Unexpected extra file
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extra = zipfile.ZipInfo("StandingOrderJob/extra.txt")
            zip_path, zip_sha, _ = build_valid_release_artifacts(root, extra_entries=[(extra, b"extra")])
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError) as ctx:
                    release_artifact.verify_zip_artifact(zip_path, zip_sha, expected_tag="v0.1.1")
            self.assertIn("unexpected file", str(ctx.exception).lower())

        # Tag vs meitou.json version mismatch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path, zip_sha, _ = build_valid_release_artifacts(root, version="0.1.1")
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    release_artifact.verify_zip_artifact(zip_path, zip_sha, expected_tag="v0.2.0")

    def test_stage_steam_rejects_tampered_or_incomplete_contents_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path, zip_sha, contents_sha = build_valid_release_artifacts(root, version="0.1.1")
            with redirect_stdout(io.StringIO()):
                verified = release_artifact.verify_zip_artifact(zip_path, zip_sha, expected_tag="v0.1.1")

            # Incomplete manifest (missing meitou.json)
            lines = contents_sha.read_text(encoding="utf-8").splitlines()
            contents_sha.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                release_artifact.stage_and_verify_steam_content(
                    zip_path, verified["zip_sha256"], contents_sha, root / "staging1"
                )

            # Wrong hash for DLL
            tampered = [
                ("0" * 64 + "  StandingOrderJob/StandingOrderJob.dll") if "dll" in ln else ln
                for ln in lines
            ]
            contents_sha.write_text("\n".join(tampered) + "\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    release_artifact.stage_and_verify_steam_content(
                        zip_path, verified["zip_sha256"], contents_sha, root / "staging2"
                    )

    def test_generate_workshop_vdf_minimal_escaping_and_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = root / "extract" / "StandingOrderJob"
            content.mkdir(parents=True)
            vdf_out = root / "workshop.vdf"

            with redirect_stdout(io.StringIO()):
                result = release_artifact.generate_workshop_vdf(
                    item_id="3840000001",
                    content_folder=content,
                    vdf_out=vdf_out,
                    changelog='Fix "quotes" and C:\\path\\slash\r\nLine 2',
                    tag="v0.1.1",
                    release_url="https://github.com/org/repo/releases/tag/v0.1.1",
                    dry_run=False,
                )

            self.assertEqual(result["item_id"], "3840000001")
            root_key, parsed = release_artifact.parse_simple_vdf(vdf_out.read_text(encoding="utf-8"))
            self.assertEqual(root_key, "workshopitem")
            self.assertEqual(parsed["appid"], "233860")
            self.assertEqual(parsed["publishedfileid"], "3840000001")
            self.assertNotIn("title", parsed)
            self.assertNotIn("description", parsed)
            self.assertNotIn("visibility", parsed)
            self.assertNotIn("previewfile", parsed)
            self.assertIn("'quotes'", parsed["changenote"])
            self.assertIn("C:\\path\\slash\n- Line 2", parsed["changenote"])
            self.assertNotIn("\\n", parsed["changenote"])

            # Blank item ID fails closed on live, allowed as placeholder on dry-run.
            with self.assertRaises(ValueError):
                release_artifact.generate_workshop_vdf(
                    item_id="",
                    content_folder=content,
                    vdf_out=vdf_out,
                    changelog="note",
                    tag="v0.1.1",
                    release_url="https://example.com",
                    dry_run=False,
                )
            with redirect_stdout(io.StringIO()):
                dry = release_artifact.generate_workshop_vdf(
                    item_id="",
                    content_folder=content,
                    vdf_out=vdf_out,
                    changelog="note",
                    tag="v0.1.1",
                    release_url="https://example.com",
                    dry_run=True,
                )
            self.assertEqual(dry["item_id"], "DRY_RUN_UNSET_ITEM_ID")

            # Zero item ID rejected even in dry-run!
            with self.assertRaises(ValueError):
                release_artifact.generate_workshop_vdf(
                    item_id="0",
                    content_folder=content,
                    vdf_out=vdf_out,
                    changelog="note",
                    tag="v0.1.1",
                    release_url="https://example.com",
                    dry_run=True,
                )

            # VDF inside contentfolder rejected!
            with self.assertRaises(ValueError):
                release_artifact.generate_workshop_vdf(
                    item_id="12345",
                    content_folder=content,
                    vdf_out=content / "workshop.vdf",
                    changelog="note",
                    tag="v0.1.1",
                    release_url="https://example.com",
                    dry_run=True,
                )

            # Optional preview validation
            png_file = root / "thumb.png"
            png_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
            with redirect_stdout(io.StringIO()):
                with_preview = release_artifact.generate_workshop_vdf(
                    item_id="3840000001",
                    content_folder=content,
                    vdf_out=vdf_out,
                    changelog="note",
                    tag="v0.1.1",
                    release_url="https://example.com",
                    dry_run=False,
                    update_preview=True,
                    preview_file=png_file,
                    staging_dir=root,
                )
            self.assertTrue(Path(with_preview["preview_path"]).is_file())
            self.assertFalse(release_artifact.is_inside_directory(Path(with_preview["preview_path"]), content))

            # Bad magic bytes rejected
            fake_png = root / "bad.png"
            fake_png.write_bytes(b"NOT-A-PNG")
            with self.assertRaises(ValueError):
                release_artifact.generate_workshop_vdf(
                    item_id="3840000001",
                    content_folder=content,
                    vdf_out=vdf_out,
                    changelog="note",
                    tag="v0.1.1",
                    release_url="https://example.com",
                    dry_run=False,
                    update_preview=True,
                    preview_file=fake_png,
                    staging_dir=root,
                )

    def test_steam_description_template_rendering_bbcode_and_vdf_limit(self) -> None:
        mod_root = SCRIPTS_DIR.parent.parent
        bbcode = release_artifact.load_steam_description(
            steam_template=mod_root / "docs" / "store" / "steam.md.jinja",
            copier_answers=mod_root / ".copier-answers.yml",
        )
        self.assertIn("[h2]Description[/h2]", bbcode)
        self.assertIn("[h3]Steam Workshop[/h3]", bbcode)
        self.assertIn("[olist]", bbcode)
        self.assertIn("[list]", bbcode)
        self.assertIn("[url=https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job]", bbcode)
        self.assertNotIn("{{", bbcode)
        self.assertNotIn("{%", bbcode)
        self.assertNotIn("## ", bbcode)
        self.assertNotIn("](https://", bbcode)
        self.assertLessEqual(len(bbcode), release_artifact.MAX_DESCRIPTION_CHARS)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = root / "extract" / "StandingOrderJob"
            content.mkdir(parents=True)
            vdf_out = root / "workshop.vdf"

            with redirect_stdout(io.StringIO()):
                res = release_artifact.generate_workshop_vdf(
                    item_id="3846152406",
                    content_folder=content,
                    vdf_out=vdf_out,
                    changelog="note",
                    tag="v0.1.0",
                    release_url="https://github.com/org/repo/releases/tag/v0.1.0",
                    dry_run=False,
                    description=bbcode,
                )
            self.assertEqual(res["description_chars"], str(len(bbcode)))
            _root_key, parsed = release_artifact.parse_simple_vdf(vdf_out.read_text(encoding="utf-8"))
            self.assertIn("description", parsed)
            self.assertIn("[h2]Description[/h2]", parsed["description"])
            self.assertNotIn("title", parsed)
            self.assertNotIn("visibility", parsed)

            # Exceeding Steam's 8000-char limit must fail closed.
            with self.assertRaises(ValueError):
                release_artifact.generate_workshop_vdf(
                    item_id="3846152406",
                    content_folder=content,
                    vdf_out=vdf_out,
                    changelog="note",
                    tag="v0.1.0",
                    release_url="https://github.com/org/repo/releases/tag/v0.1.0",
                    dry_run=False,
                    description="x" * (release_artifact.MAX_DESCRIPTION_CHARS + 1),
                )

    def test_format_changenote_avoids_parenthesized_urls_and_handles_tag_fallback(self) -> None:
        base_repo = "https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job"

        # With tag on repo URL: appends /releases/tag/<tag> and never wraps URL in parentheses.
        with_tag = release_artifact.format_changenote("", "v0.1.0", base_repo)
        self.assertEqual(
            with_tag,
            f"Release v0.1.0 - {base_repo}/releases/tag/v0.1.0",
        )
        self.assertNotIn("(http", with_tag)

        # Already tag-scoped release URL is not duplicated.
        already_tagged = release_artifact.format_changenote("", "v0.1.0", f"{base_repo}/releases/tag/v0.1.0")
        self.assertEqual(
            already_tagged,
            f"Release v0.1.0 - {base_repo}/releases/tag/v0.1.0",
        )

        # Fallback to no tag when tag is blank.
        no_tag = release_artifact.format_changenote("", "", base_repo)
        self.assertEqual(no_tag, f"Release - {base_repo}")
        self.assertNotIn("(http", no_tag)

        # Markdown links, tables, provenance tail, and parenthesized URLs are cleaned for Steam.
        body = (
            "## [0.2.0](https://github.com/org/repo/compare/v0.1.0...v0.2.0) (2026-11-26)\n\n"
            "### Features\n\n"
            "* Added feature ([#4](https://github.com/org/repo/issues/4)) ([abc1234](https://github.com/org/repo/commit/abc1234)) (https://example.com/notes)\n\n"
            "### Build provenance & checksums\n\n"
            "- **Originating CI run:** https://github.com/org/repo/actions/runs/999\n\n"
            "### Package checksums (SHA-256)\n\n"
            "| File | SHA-256 |\n"
            "| --- | --- |\n"
            "| `StandingOrderJob-0.2.0.zip` | `deadbeef` |\n"
        )
        note = release_artifact.format_changenote(body, "v0.2.0", base_repo)
        self.assertNotIn("(http", note)
        self.assertNotIn("###", note)
        self.assertNotIn("| File |", note)
        self.assertNotIn("deadbeef", note)
        self.assertIn("- Features: Added feature (#4) https://example.com/notes", note)
        self.assertIn(f"Release v0.2.0 - {base_repo}/releases/tag/v0.2.0", note)

    def test_resolve_steam_description_update_live_diff_git_fallback_and_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
            tpl_path = repo_dir / "docs" / "store" / "steam.md.jinja"
            tpl_path.parent.mkdir(parents=True)
            ans_path = repo_dir / ".copier-answers.yml"

            tpl_path.write_text("## Description\n\n{{ description | trim }}\n", encoding="utf-8")
            ans_path.write_text('description: "Version one \\"quoted\\" text."\n', encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "v0.1.0"], cwd=repo_dir, check=True)
            subprocess.run(["git", "tag", "v0.1.0"], cwd=repo_dir, check=True)

            # Live Steam API matches (even with VDF quote normalization): omits description.
            expected_bbcode = "[h2]Description[/h2]\nVersion one 'quoted' text."
            desc_same, status_same = release_artifact.resolve_steam_description_update(
                mode="auto",
                item_id="3846152406",
                tag="v0.1.1",
                steam_template=tpl_path,
                copier_answers=ans_path,
                git_dir=repo_dir,
                live_fetcher=lambda _id: (expected_bbcode + "\r\n", "ok"),
            )
            self.assertEqual(desc_same, "")
            self.assertIn("unchanged vs live Steam Workshop", status_same)

            # Live Steam API differs: includes description.
            desc_diff, status_diff = release_artifact.resolve_steam_description_update(
                mode="auto",
                item_id="3846152406",
                tag="v0.1.1",
                steam_template=tpl_path,
                copier_answers=ans_path,
                git_dir=repo_dir,
                live_fetcher=lambda _id: ("[h2]Description[/h2]\nOld live text.", "ok"),
            )
            self.assertIn("[h2]Description[/h2]", desc_diff)
            self.assertIn("changed vs live Steam Workshop", status_diff)

            # Live Steam API unavailable (private/hidden) + unchanged vs previous git tag v0.1.0: omits description.
            desc_git_same, status_git_same = release_artifact.resolve_steam_description_update(
                mode="auto",
                item_id="3846152406",
                tag="v0.1.1",
                steam_template=tpl_path,
                copier_answers=ans_path,
                git_dir=repo_dir,
                live_fetcher=lambda _id: (None, "live item not publicly readable (result=9)"),
            )
            self.assertEqual(desc_git_same, "")
            self.assertIn("unchanged vs git tag v0.1.0", status_git_same)

            # Edit description on working tree; git fallback detects change vs v0.1.0 and includes description.
            ans_path.write_text("description: Updated description text.\n", encoding="utf-8")
            desc_git_diff, status_git_diff = release_artifact.resolve_steam_description_update(
                mode="auto",
                item_id="3846152406",
                tag="v0.1.1",
                steam_template=tpl_path,
                copier_answers=ans_path,
                git_dir=repo_dir,
                live_fetcher=lambda _id: (None, "live item not publicly readable (result=9)"),
            )
            self.assertIn("Updated description text.", desc_git_diff)
            self.assertIn("changed vs git tag v0.1.0", status_git_diff)

            # Explicit skip and force modes.
            desc_skip, status_skip = release_artifact.resolve_steam_description_update(
                mode="skip",
                item_id="3846152406",
                tag="v0.1.1",
                steam_template=tpl_path,
                copier_answers=ans_path,
                git_dir=repo_dir,
            )
            self.assertEqual(desc_skip, "")
            self.assertIn("update_description=skip", status_skip)

            desc_force, status_force = release_artifact.resolve_steam_description_update(
                mode="force",
                item_id="3846152406",
                tag="v0.1.1",
                steam_template=tpl_path,
                copier_answers=ans_path,
                git_dir=repo_dir,
                live_fetcher=lambda _id: ("[h2]Description[/h2]\nUpdated description text.", "ok"),
            )
            self.assertIn("Updated description text.", desc_force)
            self.assertIn("forced", status_force)

    def test_format_nexus_changelog_emits_plain_bullet_lines_without_markdown_or_tables(self) -> None:
        release_body = """## [0.1.1](https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job/compare/v0.1.0...v0.1.1) (2026-11-26)


### Features

* **docs:** update mod descriptions ([8467d56](https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job/commit/8467d56b58885f40501e7a518738ffd8aeeb5d67))

* **ci:** release adjustments ([#12](https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job/pull/12))

### Build provenance & checksums

- **Originating CI run:** https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job/actions/runs/45898934898
- **Source commit:** https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job/commit/0448cd3c9c04d884273b2fc32fd33d043f831e15 (`0448cd3c9c04d884273b2fc32fd33d043f831e15`)

### Package checksums (SHA-256)

| File | SHA-256 |
| --- | --- |
| `StandingOrderJob-0.1.1.zip` | `3021817c9e50283a` |
"""
        formatted = release_artifact.format_nexus_changelog(
            changelog=release_body,
            tag="v0.1.1",
            release_url="https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job/releases/tag/v0.1.1",
            commit_sha="0448cd3c9c04d884273b2fc32fd33d043f831e15",
            repo="GardenGoblin-Kenshi-Mods/Standing-Order-Job",
        )
        lines = formatted.splitlines()
        self.assertEqual(
            lines,
            [
                "Features: docs: update mod descriptions",
                "Features: ci: release adjustments (#12)",
                "GitHub Release (checksums & attestations): https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job/releases/tag/v0.1.1",
                "Source commit: https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job/commit/0448cd3c9c04d884273b2fc32fd33d043f831e15",
                "Originating CI run: https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job/actions/runs/45898934898",
            ],
        )
        for line in lines:
            self.assertFalse(line.startswith(("#", "*", "-", "|")))
            self.assertNotIn("**", line)
            self.assertNotIn("`", line)

        # Fallback when release notes have no change items.
        fallback = release_artifact.format_nexus_changelog(
            changelog="",
            tag="v0.1.0",
            release_url="https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job/releases/tag/v0.1.0",
        )
        self.assertEqual(
            fallback.splitlines(),
            [
                "Release v0.1.0",
                "GitHub Release (checksums & attestations): https://github.com/GardenGoblin-Kenshi-Mods/Standing-Order-Job/releases/tag/v0.1.0",
            ],
        )


class NexusPreflightTests(unittest.TestCase):
    def test_extract_web_mod_id_handles_v3_and_game_scoped_ids(self) -> None:
        self.assertEqual(nexus_preflight.extract_web_mod_id("3161095932164"), "2308")
        self.assertEqual(nexus_preflight.extract_web_mod_id("2308"), "2308")
        self.assertEqual(nexus_preflight.extract_web_mod_id(""), "")

    def test_available_version_passes(self) -> None:
        payload = {
            "data": {
                "versions": [
                    {"id": "3161095935001", "game_scoped_id": "6847", "version": "0.1.0", "category": "main"}
                ]
            }
        }
        seen_args: list[tuple[str, str]] = []

        def fake_fetcher(file_id: str, api_key: str) -> dict:
            seen_args.append((file_id, api_key))
            return payload

        rc, outputs, msg = nexus_preflight.run_preflight(
            game_domain="kenshi",
            file_id="6847",
            target_version="0.1.1",
            api_key="  secret-key\n",
            dry_run=False,
            mod_id="3161095932164",
            fetcher=fake_fetcher,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(seen_args, [("6847", "secret-key")])
        self.assertEqual(outputs["preflight_status"], "available")
        self.assertEqual(outputs["web_mod_id"], "2308")
        self.assertIn("v0.1.1", msg)

    def test_duplicate_version_blocks_live_and_reports_in_dry_run(self) -> None:
        payload = {
            "data": {
                "versions": [
                    {"id": "3161095935001", "game_scoped_id": "6847", "version": "0.1.0", "category": "old_version"},
                    {"id": "3161095935002", "game_scoped_id": "6890", "version": "v0.1.1", "category": "archived"},
                ]
            }
        }
        rc_live, out_live, msg_live = nexus_preflight.run_preflight(
            game_domain="kenshi",
            file_id="6847",
            target_version="0.1.1",
            api_key="secret",
            dry_run=False,
            mod_id="3161095932164",
            fetcher=lambda _f, _k: payload,
        )
        self.assertEqual(rc_live, 1)
        self.assertEqual(out_live["preflight_status"], "blocked_duplicate")
        self.assertEqual(out_live["existing_version_id"], "3161095935002")
        self.assertEqual(out_live["existing_file_id"], "6847")
        self.assertEqual(out_live["web_mod_id"], "2308")
        self.assertIn("https://www.nexusmods.com/kenshi/mods/2308?tab=files&file_id=6890", out_live["existing_file_url"])
        self.assertIn("Nexus publish blocked: version v0.1.1 already exists for mod file 6847", msg_live)
        self.assertIn("no upload attempted", msg_live)

        rc_dry, out_dry, _msg_dry = nexus_preflight.run_preflight(
            game_domain="kenshi",
            file_id="6847",
            target_version="0.1.1",
            api_key="secret",
            dry_run=True,
            mod_id="3161095932164",
            fetcher=lambda _f, _k: payload,
        )
        self.assertEqual(rc_dry, 0)
        self.assertEqual(out_dry["preflight_status"], "blocked_duplicate")

    def test_api_or_ambiguous_data_fails_closed(self) -> None:
        def failing_fetcher(_f: str, _k: str) -> dict:
            raise RuntimeError("Nexus API HTTP error 503: Service Unavailable")

        rc_err, out_err, _ = nexus_preflight.run_preflight(
            "kenshi", "6847", "0.1.2", "secret", False, fetcher=failing_fetcher
        )
        self.assertEqual(rc_err, 1)
        self.assertEqual(out_err["preflight_status"], "error")

        # Missing 'data' object
        rc_no_data, _, _ = nexus_preflight.run_preflight(
            "kenshi", "6847", "0.1.2", "secret", False, fetcher=lambda _f, _k: {"unexpected": []}
        )
        self.assertEqual(rc_no_data, 1)

        # Missing 'data.versions' list
        rc_bad, _, _ = nexus_preflight.run_preflight(
            "kenshi", "6847", "0.1.2", "secret", False, fetcher=lambda _f, _k: {"data": {"unexpected": []}}
        )
        self.assertEqual(rc_bad, 1)

        # Pagination indicator fails closed (both root and data level)
        rc_page, _, _ = nexus_preflight.run_preflight(
            "kenshi",
            "6847",
            "0.1.2",
            "secret",
            False,
            fetcher=lambda _f, _k: {"data": {"versions": [], "has_more": True}},
        )
        self.assertEqual(rc_page, 1)

        rc_page_root, _, _ = nexus_preflight.run_preflight(
            "kenshi",
            "6847",
            "0.1.2",
            "secret",
            False,
            fetcher=lambda _f, _k: {"data": {"versions": []}, "next_cursor": "abc"},
        )
        self.assertEqual(rc_page_root, 1)

        # Missing version string in entry fails closed
        rc_null, _, _ = nexus_preflight.run_preflight(
            "kenshi", "6847", "0.1.2", "secret", False, fetcher=lambda _f, _k: {"data": {"versions": [{"id": "1"}]}}
        )
        self.assertEqual(rc_null, 1)

    def test_missing_credentials_skips_in_dry_run_and_fails_live(self) -> None:
        rc_dry, out_dry, _ = nexus_preflight.run_preflight("kenshi", "", "0.1.2", "", dry_run=True)
        self.assertEqual(rc_dry, 0)
        self.assertEqual(out_dry["preflight_status"], "skipped_no_credentials")

        rc_live, out_live, _ = nexus_preflight.run_preflight("kenshi", "", "0.1.2", "", dry_run=False)
        self.assertEqual(rc_live, 1)
        self.assertEqual(out_live["preflight_status"], "error")

    def test_v3_fetch_uses_v3_url_stripped_apikey_and_never_logs_error_body(self) -> None:
        import urllib.error

        captured_req = {}

        class _FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self) -> bytes:
                return b'{"data":{"versions":[]}}'

        def fake_urlopen(req, timeout=0):
            captured_req["url"] = req.full_url
            captured_req["headers"] = {k.lower(): v for k, v in req.header_items()}
            return _FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = nexus_preflight.fetch_mod_file_versions_json("6847", "  my-secret-token \n")
        self.assertEqual(res, {"data": {"versions": []}})
        self.assertEqual(captured_req["url"], "https://api.nexusmods.com/v3/mod-files/6847/versions")
        self.assertEqual(captured_req["headers"]["apikey"], "my-secret-token")

        err = urllib.error.HTTPError(
            url="https://api.nexusmods.com/v3/mod-files/6847/versions",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"detail":"DO_NOT_LEAK_SENSITIVE_BODY"}'),
        )
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as ctx:
                nexus_preflight.fetch_mod_file_versions_json("6847", "my-secret-token")
        self.assertEqual(str(ctx.exception), "Nexus API HTTP error 401: Unauthorized")
        self.assertNotIn("DO_NOT_LEAK_SENSITIVE_BODY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
