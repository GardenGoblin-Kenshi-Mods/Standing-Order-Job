# release-please for generated mods

This template optionally ships [googleapis/release-please](https://github.com/googleapis/release-please) into each generated Meitou mod.

## Files

| File | Role |
| --- | --- |
| `release-please-config.json` | Manifest-driven config (`simple` + `meitou.json` version extra-file) |
| `.release-please-manifest.json` | Last-released version map (starts at `0.0.0`) |
| `version.txt` | Version file required by the `simple` strategy |
| `.github/workflows/release-please.yml` | Action on push to `main` / `master` and `workflow_dispatch` |

Toggle with Copier answer `include_release_please` (default `true`).

## Operator flow

1. Land Conventional Commits on the default branch (`feat:` / `fix:` / breaking `!`).
2. Let the workflow open or update the release PR (`autorelease: pending`).
3. Review changelog + version bumps (`version.txt`, `meitou.json`).
4. Merge the release PR → tag + GitHub Release.

### Org permission / token

Org Actions settings must allow **read and write** workflow permissions and **Allow GitHub Actions to create and approve pull requests**. With those on, `GITHUB_TOKEN` is enough.

Optional repo secret `RELEASE_PLEASE_TOKEN` (fine-grained PAT: Contents + Pull requests write) overrides `GITHUB_TOKEN` if the org tightens permissions again.

Factory skill: `meitou-mod-release-please` (under Kenshi-Mods `.agents/skills/`).
