---
description: How releases are cut — push a vX.Y.Z tag, CI builds the wheel and publishes the GitHub Release
paths:
  - ".github/workflows/release.yml"
  - "pyproject.toml"
---

# Releasing Clarinet

Releases are fully automated by `.github/workflows/release.yml`, triggered on
`push` of any `v*` tag. Every published release (author `github-actions[bot]`)
came through this path — don't build or upload wheels by hand.

## What the workflow does (on tag push)

1. Build frontend — `bash scripts/build_frontend.sh` (needs gleam + bun).
2. **Validate `tag == pyproject.toml version`** — fails the run on mismatch.
   So the version bump commit MUST be the one you tag.
3. `uv build --wheel`, then verify `clarinet/static/` is inside the wheel.
4. `gh release create "$TAG" --generate-notes dist/*.whl`.

## Cutting a release

```bash
# bump version in pyproject.toml, commit (uv.lock picks up the same bump)
git tag -a vX.Y.Z -m "Clarinet vX.Y.Z"   # tag the version-bump commit
git push origin <branch> vX.Y.Z          # push branch + tag → CI does the rest
```

No local `make build` / `uv build` needed — CI rebuilds. Build locally only to
verify a wheel *before* tagging (`make frontend-build` then `uv build --wheel`);
`clarinet/static/` is gitignored, so it's absent in a fresh worktree and `uv
build` alone would ship a frontend-less wheel.

## Pitfalls

- **Never also run `gh release create vX.Y.Z` manually.** Creating the tag
  (pushed or via the release API) already fires `on: push: tags: v*`; a second
  release-create collides with the CI one.
- **Tag the bump commit, not an earlier one** — the tag-vs-version check is
  strict (`vX.Y.Z` ↔ `version = "X.Y.Z"`).

## Hotfix on an older line (e.g. 0.8.x while main is 0.10.x)

```bash
git worktree add -b fix/<slug>-X.Y.Z <path> vX.Y.(Z-1)   # branch from last tag of the line
# fix + bump pyproject to X.Y.Z + add test, commit, then tag + push as above
```

A fix landed on a hotfix branch is **not** in `main` — cherry-pick it to main
separately, or main keeps the bug.
