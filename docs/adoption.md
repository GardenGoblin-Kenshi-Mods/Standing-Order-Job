# Adopting Copier on an existing mod

Existing Factory mods were authored by hand. `copier update` expects `.copier-answers.yml` and template provenance from a prior `copier copy`.

## Safe approach

1. Generate a fresh sample from the current template tag into a disposable directory.
2. Diff the sample against the existing mod; decide which files should become template-managed.
3. Either:
   - **Re-scaffold (preferred for small mods):** create a new branch, copy answers, manually align paths, then commit `.copier-answers.yml` after a careful review; or
   - **Initialize answers only:** add a hand-written `.copier-answers.yml` matching the template's question keys, then run `copier update` on a feature branch and resolve every conflict in review.
4. Never bulk-migrate or push template updates to protected default branches without a PR.

## Do not assume

- That file paths or class names already match Jinja outputs.
- That `copier update` is non-destructive without a reviewable diff.
- That Steam/Nexus workflows from issues #4/#5 exist yet.
