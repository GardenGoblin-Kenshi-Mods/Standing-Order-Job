# Agent notes — Standing Order Job

- Prefer project-wide skills if this mod is checked out as part of a superproject.
- Treat `docs/behaviors.md` as the behavioral North Star; update it alongside any behavior change, verify against its Verification Matrix, and keep `README.md` (`## Features`) synchronized with `.copier-answers.yml`.
- Keep gameplay logic in `src/`; keep packaging in `tools/` and free of `KENSHI_PATH` dependencies so CI succeeds.
- Keep documentation links standalone-repo-safe (no monorepo-relative `../<sibling>/...` paths or placeholder store URLs).
- After package-affecting changes, run `./tools/package.sh` then `./tools/stage.sh` with `MEITOU_CLIENT_ROOT` and `KENSHI_PATH` set.
- Gate diagnostics behind `MEITOU_STANDING_ORDER_JOB_DEBUG=1`; never spam the player with debug messages.
- Do not commit original Kenshi files, Workshop downloads, saves, or credentials.
- Prefer Conventional Commits (`feat:`, `fix:`, …) so `release-please` tracks SemVer releases and `CHANGELOG.md` cleanly.
