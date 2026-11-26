#!/usr/bin/env python3
"""Unit tests for SteamCMD CI session handling and log evaluation helpers."""

from __future__ import annotations

import io
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import steam_ci_helpers as helpers  # noqa: E402


SAMPLE_CONFIG_VDF = """"InstallConfigStore"
{
\t"Software"
\t{
\t\t"Valve"
\t\t{
\t\t\t"Steam"
\t\t\t{
\t\t\t\t"ConnectCache"
\t\t\t\t{
\t\t\t\t\t"1234abcd" "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.fakeTokenPayloadValue1234567890"
\t\t\t\t}
\t\t\t}
\t\t}
\t}
}
"""


class SteamCiHelpersTests(unittest.TestCase):
    def test_validate_config_vdf_accepts_valid_config(self) -> None:
        size, count = helpers.validate_config_vdf_text(SAMPLE_CONFIG_VDF)
        self.assertGreater(size, 0)
        self.assertEqual(count, 1)

    def test_validate_config_vdf_rejects_empty_and_missing_sections(self) -> None:
        with self.assertRaises(ValueError):
            helpers.validate_config_vdf_text("")
        with self.assertRaises(ValueError):
            helpers.validate_config_vdf_text('"OtherRoot" { }')
        with self.assertRaises(ValueError):
            helpers.validate_config_vdf_text('"InstallConfigStore" { "ConnectCache" { } }')
        with self.assertRaises(ValueError):
            helpers.validate_config_vdf_text(SAMPLE_CONFIG_VDF, max_bytes=10)

    def test_restore_config_vdf_writes_0600_and_deduplicates_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            steam_home = root / "steam-home"
            local_steam = steam_home / ".local" / "share" / "Steam"
            local_steam.mkdir(parents=True)
            (steam_home / ".steam").mkdir(parents=True)
            (steam_home / ".steam" / "steam").symlink_to(local_steam)

            steamcmd_dir = root / "steamcmd"
            (steamcmd_dir / "config").mkdir(parents=True)

            buf = io.StringIO()
            with redirect_stdout(buf):
                written = helpers.restore_config_vdf(
                    SAMPLE_CONFIG_VDF,
                    steam_home=steam_home,
                    steamcmd_dir=steamcmd_dir,
                    emit_gh_masks=True,
                )

            # .steam/steam resolves to .local/share/Steam, so only 2 distinct files are written.
            self.assertEqual(len(written), 2)
            for path in written:
                self.assertEqual(path.read_text(encoding="utf-8"), SAMPLE_CONFIG_VDF)
                mode = stat.S_IMODE(path.stat().st_mode)
                self.assertEqual(mode, 0o600)
                parent_mode = stat.S_IMODE(path.parent.stat().st_mode)
                self.assertEqual(parent_mode, 0o700)

            out = buf.getvalue()
            self.assertIn("::add-mask::eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.fakeTokenPayloadValue1234567890", out)
            self.assertNotIn("InstallConfigStore", out)

    def test_sanitize_steam_log_redacts_username_and_tokens(self) -> None:
        raw = "\x1b[1mLogging in user 'GoblinBot' to Steam Public...OK\x1b[0m\ntoken=abcdef0123456789abcdef0123456789\n"
        lines = helpers.sanitize_steam_log(raw, username="GoblinBot")
        self.assertEqual(len(lines), 2)
        self.assertNotIn("GoblinBot", lines[0])
        self.assertIn("<STEAM_USERNAME>", lines[0])
        self.assertNotIn("abcdef0123456789", lines[1])
        self.assertIn("<REDACTED>", lines[1])

    def test_evaluate_auth_log_passes_on_ok_login_variants(self) -> None:
        for sample in (
            "Logging in user 'GoblinBot' to Steam Public...OK\nWaiting for user info...OK\n",
            "Logging in using cached credentials.\nLogging in user 'GoblinBot' [U:1:123456789] to Steam Public...OK\nWaiting for client config...OK\n",
            "Logged in OK\nWaiting for user info...OK\n",
        ):
            passed, reason, lines = helpers.evaluate_auth_log(sample, exit_code=0, username="GoblinBot")
            self.assertTrue(passed, msg=reason)
            self.assertTrue(all("GoblinBot" not in ln for ln in lines))
            self.assertTrue(all("123456789" not in ln for ln in lines))

    def test_evaluate_auth_log_rejects_failures_and_silent_exit_zero(self) -> None:
        cases = [
            ("Steam Guard code:", 124),
            ("FAILED (Invalid Password)", 5),
            ("Cached credentials not found.", 5),
            ("FAILED (Rate Limit Exceeded)", 5),
            ("ERROR! Login failure", 0),
            ("Loading Steam API...OK.\n", 0),  # exit 0 without Logged in OK
        ]
        for sample, code in cases:
            passed, _reason, _lines = helpers.evaluate_auth_log(sample, exit_code=code, username="GoblinBot")
            self.assertFalse(passed, msg=f"Expected failure for exit={code}, sample={sample!r}")

    def test_evaluate_upload_log(self) -> None:
        passed, _, _ = helpers.evaluate_upload_log("Preparing update...\nSuccess.\n", exit_code=0)
        self.assertTrue(passed)

        failed_err, _, _ = helpers.evaluate_upload_log("ERROR! Failed to update workshop item (Access Denied)\n", exit_code=0)
        self.assertFalse(failed_err)

        failed_no_success, _, _ = helpers.evaluate_upload_log("Preparing update...\n", exit_code=0)
        self.assertFalse(failed_no_success)

        failed_timeout, _, _ = helpers.evaluate_upload_log("Success.\n", exit_code=124)
        self.assertFalse(failed_timeout)

    def test_cli_check_auth_log_writes_github_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_file = root / "auth.log"
            log_file.write_text("Logging in user 'GoblinBot' to Steam Public...OK\n", encoding="utf-8")
            gh_out = root / "github_output.txt"

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = helpers.main([
                    "check-auth-log",
                    "--log-file", str(log_file),
                    "--exit-code", "0",
                    "--username", "GoblinBot",
                    "--github-output", str(gh_out),
                ])

            self.assertEqual(rc, 0)
            output_text = gh_out.read_text(encoding="utf-8")
            self.assertIn("auth_status=pass", output_text)
            self.assertNotIn("GoblinBot", buf.getvalue())

    def test_cli_inspect_config_does_not_leak_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.vdf"
            cfg.write_text(SAMPLE_CONFIG_VDF, encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = helpers.main(["inspect-config", "--file", str(cfg)])
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("ConnectCache=present(1)", out)
            self.assertNotIn("fakeTokenPayloadValue", out)


if __name__ == "__main__":
    unittest.main()
