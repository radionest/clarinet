---
globs:
  - ".github/workflows/**"
---

## CI/workflow debugging

When diagnosing CI failures, always start with external state before reading code:

1. `gh run list --workflow=<name>.yml --limit=5` — recent run status
2. `gh run view <id> --log-failed` — actual error from the failed step
3. Only then read the workflow YAML and related code

Do NOT delegate CI debugging to the Explore agent — it cannot access `gh` CLI or GitHub Actions logs. Use direct tool calls instead.
