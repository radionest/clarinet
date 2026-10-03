---
description: Bump project version (auto-increment or explicit), commit, tag, push to main
argument-hint: "[version]"
allowed-tools:
  - Bash
disable-model-invocation: true
---

Bump the project version, commit, tag, and push.

## Usage

- `/bump` — auto-increment (0.0a.19 → 0.0a.20)
- `/bump 0.0a.25` — set explicit version

## Instructions

1. You MUST be on the `main` branch with a clean working tree. If in a worktree — exit first.
2. Run: `bash scripts/bump.sh $ARGUMENTS`
3. Report the result to the user.

Do NOT use Edit/Write tools — the script handles everything via sed + git.
