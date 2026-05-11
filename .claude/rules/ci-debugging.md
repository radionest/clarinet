---
description: CI/GitHub Actions debugging workflow — start with gh CLI external state, not code
paths:
  - ".github/workflows/**"
---

## CI/workflow debugging

When diagnosing CI failures, always start with external state before reading code:

0. **Before declaring a flake or rerunning** — check the PR branch is current with `main`:
   `git fetch origin main && git log --oneline origin/main..HEAD origin/main ^HEAD`.
   If `main` has fixes touching the failing area (models, fixtures, services), `git rebase origin/main` first. Random integration failures on a stale branch are usually merged-fix collisions, not flakes.
   **Rebase requires no tracked-file changes** (untracked files don't block it). Pre-check with `git diff --quiet && git diff --cached --quiet` (exit 0 → safe to rebase); on non-zero, commit or stash before `git rebase`, otherwise it aborts with `cannot rebase: You have unstaged changes`.
1. `gh run list --workflow=<name>.yml --limit=5` — recent run status
2. `gh run view <id> --log-failed` — actual error from the failed step
3. Only then read the workflow YAML and related code

Do NOT delegate CI debugging to the Explore agent — it cannot access `gh` CLI or GitHub Actions logs. Use direct tool calls instead.
