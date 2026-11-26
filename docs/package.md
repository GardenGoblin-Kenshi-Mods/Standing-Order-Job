# Standing Order Job — package, staging, and debug

## Acceptance brief

Authoritative player-visible behaviors, edge cases, and the verification matrix live in [`docs/behaviors.md`](behaviors.md). Mod-specific implementation invariants live in [`docs/implementation-notes.md`](implementation-notes.md).

## Package

- **Classification:** Meitou C# code mod (`apiVersion` 3; `TaskType 1000` named `Stand Here`).
- **Compatible range:** `>=0.23.0 <0.24.0` (`meitou.json`).

### Requirements & dependencies

- **[Meitou Client](https://github.com/VlcekM/MeitouClient)**: matching `gameVersion` `>=0.23.0 <0.24.0`.

Intended load order:

```text
StandingOrderJob.mod
```

### Build and stage (pinned Nix build)

The canonical Linux and GitHub Actions build path uses the pinned offline Nix flake (`flake.nix`, `flake.lock`, `nix/deps.json`). On Windows, run Nix inside **WSL2** (`experimental-features = nix-command flakes`, `sandbox = true`).

Uses a disposable symlink and `out/staging/mods.cfg` only; never edits a player's live `data/mods.cfg`:

```bash
export MEITOU_CLIENT_ROOT=/path/to/MeitouClient
export KENSHI_PATH=/path/to/Kenshi
./tools/package.sh            # builds via pinned Nix flake (or --local-meitou for local MeitouClient)
./tools/verify-reproducibility.sh
./tools/test.sh
./tools/stage.sh
```

- `out/StandingOrderJob/` contains `StandingOrderJob.mod`, `meitou.json`, and `StandingOrderJob.dll` (compiled in `Release` with `Optimize=true`, `Deterministic=true`, `DebugType=embedded`, `EmbedAllSources=true`, and Source Link).
- `out/StandingOrderJob-<version>.zip` is built deterministically by `tools/archive-package.sh`, alongside non-circular `StandingOrderJob-<version>.contents.sha256` and `StandingOrderJob-<version>.zip.sha256` manifests and `out/StandingOrderJob.binlog` (scanned by `tools/check-binlog-secrets.sh`).
- If NuGet dependencies or `.github/meitou-client.ref` change, refresh `nix/deps.json` with `./tools/update-nix-deps.sh`.
- Unit, save/reload integration, and headless behavioral tests (`tests/StandingOrderJob.Tests/`) run locally via `./tools/test.sh`.

## Release provenance, Nexus Mods & Steam Workshop publishing

- `.github/workflows/release-please.yml` builds with the pinned Nix flake, verifies `StandingOrderJob.binlog` is free of secrets/host paths, attests both `out/StandingOrderJob/StandingOrderJob.dll` and `out/StandingOrderJob-<version>.zip` via `actions/attest-build-provenance@v2`, uploads the ZIP, `.sha256` manifests, and `.binlog` to the GitHub Release, and links the originating CI run, commit, tag, and `README.md#build-transparency--verification`.
- Both `.github/workflows/publish-nexus.yml` and `.github/workflows/publish-steam.yml` consume the exact same already-published, immutable GitHub Release ZIP without rebuilding or altering it. Both require `StandingOrderJob-<version>.zip.sha256` to be present and valid (fail closed on missing or mismatched checksums), validate safe ZIP paths and `meitou.json.version == tag`, and support `workflow_dispatch` with `release_tag` (`default: latest`, resolving blank/`latest` to the latest published GitHub Release and validating any explicit tag against full Git tag history + published GitHub Release without silent fallback) and `dry_run` (`default: true`).
- Note on attestation verification: `gh attestation verify` works publicly once the repository is public; while private, verification requires authenticated read access and a GitHub Team/Enterprise plan. Local `sha256sum -c` checks in publish workflows verify artifact integrity against the published release manifests; build provenance is attested separately via GitHub attestations.

### Nexus Mods publishing (`publish-nexus.yml`)

Nexus Mods v3 file uploads update an existing file chain on an already-created mod page, so create the mod page and upload the initial file once on Nexus Mods first, then configure:

- **Repository secret:** `NEXUSMODS_API_KEY` (personal API key from Nexus Mods *Site preferences → API keys*).
- **GitHub Actions environment (`nexus_deploy`) variables:** `NEXUSMODS_MOD_ID` (numeric mod ID from the Nexus mod URL) and `NEXUSMODS_FILE_ID` (file ID from the Nexus *Files → Advanced* / *Manage Files* view).

**Duplicate-version preflight:** Before any live Nexus upload, `.github/scripts/nexus_preflight.py` queries `GET /v1/games/kenshi/mods/<NEXUSMODS_MOD_ID>/files.json` and fails closed (nonzero exit, zero upload attempted) if the target version already exists on the mod or if the API cannot be checked reliably. Dry runs report the preflight result when credentials are configured, or clearly note that credentialed API preflight was skipped.

```bash
# Safe dry-run against the latest published GitHub Release
gh workflow run publish-nexus.yml -R GardenGoblin-Kenshi-Mods/Standing-Order-Job

# Live upload for a specific verified release tag
gh workflow run publish-nexus.yml -R GardenGoblin-Kenshi-Mods/Standing-Order-Job \
  -f release_tag=v0.1.0 -f dry_run=false
```

### Steam Workshop publishing (`publish-steam.yml`)

Steam Workshop publishing uses a repository-owned SteamCMD wrapper (`.github/scripts/steam-ci.sh`, no third-party publishing action) and a dedicated Steam publishing account authenticated via a cached `config.vdf` session stored in GitHub Actions secrets.

- **Repository secrets:** `STEAM_USERNAME` (dedicated publishing account login name) and `STEAM_CONFIG_VDF` (raw contents of `config.vdf`, no Base64 needed).
- **GitHub Actions environment (`steam_deploy`) variable:** `STEAM_WORKSHOP_ITEM_ID` (nonzero numeric Workshop item ID). Automated CI always rejects blank, `0`, or non-numeric IDs so it can never accidentally create duplicate listings.

#### 1. Create and verify a local SteamCMD session

Run the Nix-wrapped local login helper (requires Linux/WSL2 `x86_64` with Nix; keeps all writable SteamCMD state isolated in `${XDG_STATE_HOME:-~/.local/state}/standing-order-job-steamcmd` outside the Nix store and git repo):

```bash
./tools/steam-login.sh --username <dedicated_steam_username>
```

The script:
1. Launches an interactive `nixpkgs#steamcmd` session inside a minimal `multiArch` FHS wrapper so you enter password and Steam Guard code directly (never passed on CLI arguments).
2. Runs a second non-interactive verification login (`+@NoPromptForPassword 1`, stdin closed) to confirm the cached token works without prompting.
3. Locates `config.vdf` (`chmod 600`), verifies a `ConnectCache` token is present and within GitHub Actions' 48 KiB secret limit, and lists observed session files without printing credentials.

#### 2. Upload raw secrets (no Base64 required)

```bash
# Re-verify and pipe raw config.vdf directly into STEAM_CONFIG_VDF (never prints contents)
./tools/steam-login.sh --username <dedicated_steam_username> --verify-only --upload-secret

# Set the dedicated publishing account username secret once
gh secret set STEAM_USERNAME -R GardenGoblin-Kenshi-Mods/Standing-Order-Job
```

> **Security note:** `STEAM_CONFIG_VDF` contains live session tokens and is multiline; GitHub Actions masks the entire multiline value rather than individual lines. Never print, `cat`, or upload `config.vdf` as an artifact, and never enable shell `set -x` around authenticated steps.

#### 3. Validate portable CI authentication on fresh runners

Run `.github/workflows/validate-steam-session.yml` **twice** on two separate fresh `ubuntu-latest` runners without refreshing `STEAM_CONFIG_VDF`:

```bash
gh workflow run validate-steam-session.yml -R GardenGoblin-Kenshi-Mods/Standing-Order-Job
```

Both runs must report `pass` in the Actions summary. Live `publish-steam.yml` also runs this exact SteamCMD authentication check before invoking `workshop_build_item`.

#### 4. First-publication bootstrap, media & gallery management

1. Create the initial Kenshi Steam Workshop listing once (via Kenshi's native FCS Workshop uploader or an interactive SteamCMD bootstrap with `publishedfileid=0`), confirm ownership by the dedicated publishing account, and record the resulting ID in repository variable `STEAM_WORKSHOP_ITEM_ID`.
2. Curate the Workshop page title, visibility, primary thumbnail, and **additional screenshot gallery** manually in the Steam Workshop web UI (`assets/screenshots/standing_order_context_menu.png` can be used as source art, subject to Steam's 1 MiB image cap).
   - Routine `publish-steam.yml` uploads render `docs/store/steam.md.jinja` from `.copier-answers.yml`, convert it to Steam Workshop BBCode, and include `description` alongside `appid=233860`, `publishedfileid`, `contentfolder`, and `changenote` so the store description stays in sync with the repository. `title`, `visibility`, and `previewfile` remain omitted by default so manually curated artwork and visibility are never overwritten.
   - `previewfile` updates only the single primary thumbnail (not the screenshot gallery); set `-f update_preview=true -f preview_file=<repo-relative-path>` only for an intentional primary thumbnail change.
3. Verify extracted staging & real subscription loading: `publish-steam.yml` extracts the verified ZIP, validates all three runtime files against `StandingOrderJob-<version>.contents.sha256` byte-for-byte, and uploads `StandingOrderJob/` contents so `StandingOrderJob.mod`, `meitou.json`, and `StandingOrderJob.dll` sit directly at `steamapps/workshop/content/233860/<STEAM_WORKSHOP_ITEM_ID>/` (required by Meitou's top-level Workshop mod discovery). Subscribe on Steam and verify hashes + Meitou loading before wiring `publish-steam` into `release-please.yml`.

#### 5. Manual dry-run, retry & rollback

Unlike Nexus Mods, live Steam publishing **intentionally allows republishing the same release or rolling back to an older verified GitHub Release** (replacing the Workshop item's current content for subscribers; no Steam-side version check).

```bash
# Safe dry-run against the latest published GitHub Release (requires no Steam credentials)
gh workflow run publish-steam.yml -R GardenGoblin-Kenshi-Mods/Standing-Order-Job

# Live Steam upload (or rollback) for a specific verified release tag
gh workflow run publish-steam.yml -R GardenGoblin-Kenshi-Mods/Standing-Order-Job \
  -f release_tag=v0.1.0 -f dry_run=false
```

#### 6. Rotating, revoking & troubleshooting

- **Expired/rejected Steam session:** Re-run `./tools/steam-login.sh --reset --username <dedicated_steam_username> --upload-secret`, then run `validate-steam-session.yml`.
- **Revoke Steam session:** Change the dedicated Steam account password or deauthorize devices in Steam Guard settings, delete/overwrite `STEAM_CONFIG_VDF` in GitHub secrets, and remove `~/.local/state/standing-order-job-steamcmd`.
- **Steam failure isolation:** Steam and Nexus publishing run independently; a Steam failure never invalidates a GitHub Release or blocks Nexus publishing, and either destination can be retried independently with `workflow_dispatch`.
- **Nexus duplicate error:** If `publish-nexus.yml` fails with `Nexus publish blocked: version vX.Y.Z already exists`, the version is already published on Nexus; do not force-overwrite — cut a new release if changes are needed.

## Debug trace

Set `MEITOU_STANDING_ORDER_JOB_DEBUG=1` before launching Meitou. The mod writes transition lines to Meitou's `log.txt` prefixed with `mod       standing-order-job:` (command receipt, assign, walk/hold/stop, prune). It never posts player-facing diagnostic messages.
