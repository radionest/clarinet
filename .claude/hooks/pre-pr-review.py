#!/usr/bin/env python3
"""
PreToolUse(Bash) hook: requires pr-diff-reviewer before gh pr create.
Blocks gh pr create unless /tmp/pr-diff-review-<branch>.done contains current HEAD SHA.

Configure via pre-pr-review.json in the same directory.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NotRequired, TypedDict


class Config(TypedDict):
    enforce_review: bool
    allow_emergency_bypass: bool
    require_worktree: bool
    branch_exclusions: list[str]
    incremental_mode: NotRequired[dict]  # type: ignore


def load_config() -> Config:
    """Load configuration from pre-pr-review.json in the same directory."""
    script_dir = Path(__file__).parent
    config_path = script_dir / "pre-pr-review.json"

    default_config: Config = {
        "enforce_review": True,
        "allow_emergency_bypass": True,
        "require_worktree": True,
        "branch_exclusions": ["main", "master"],
        "incremental_mode": {
            "enabled": True,
            "max_total_changes": 20,
            "require_same_files": True,
        },
    }

    if config_path.exists():
        with open(config_path) as f:
            user_config = json.load(f)
        incremental_mode = {
            **default_config["incremental_mode"],
            **user_config.get("incremental_mode", {}),
        }
        default_config.update(user_config)
        default_config["incremental_mode"] = incremental_mode
        return default_config
    return default_config


def get_command() -> str:
    """Read the Bash command from the PreToolUse JSON payload on stdin."""
    raw = sys.stdin.read().strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    return data.get("tool_input", {}).get("command", "")


def normalize_command(cmd: str) -> list[str]:
    """
    Strip string literals and split by shell separators.
    Returns lines that may start with actual commands.
    """
    # Remove single-quoted and double-quoted string literals
    no_strings = re.sub(r"'[^']*'", "", cmd)
    no_strings = re.sub(r'"[^"]*"', "", no_strings)

    # Split by &&, ||, ;, |
    parts = re.split(r"&&|\|\||[;|]", no_strings)
    return [p.strip() for p in parts if p.strip()]


def is_gh_pr_create(lines: list[str]) -> bool:
    """Check if any line starts with 'gh pr create'."""
    pattern = re.compile(r"^\s*gh\s+pr\s+create(\s|$)")
    return any(pattern.search(line) for line in lines)


def is_harmless_variant(lines: list[str]) -> bool:
    """Check for --help or --dry-run flags."""
    pattern = re.compile(r"^\s*gh\s+pr\s+create\b.*(--help|--dry-run)")
    return any(pattern.search(line) for line in lines)


def run_git(cmd: list[str]) -> str:
    """Run git command, return stdout stripped. Returns empty string on failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5)
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def check_incremental_mode(reviewed_sha: str, head: str, config: Config) -> tuple[bool, str]:
    """
    Check if changes since reviewed SHA are small enough to skip re-review.
    Returns (allowed, reason).
    """
    if not config.get("incremental_mode", {}).get("enabled", True):
        return False, "Incremental mode is disabled"

    inc_config = config["incremental_mode"]
    max_changes = inc_config.get("max_total_changes", 20)
    require_same_files = inc_config.get("require_same_files", True)

    merge_base = run_git(["git", "merge-base", "origin/main", reviewed_sha])
    if not merge_base:
        return False, "Cannot determine merge base"

    # Get lists of files
    reviewed_files = set(
        run_git(["git", "diff", "--name-only", merge_base, reviewed_sha]).splitlines()
    )
    new_files = set(run_git(["git", "diff", "--name-only", reviewed_sha, head]).splitlines())

    if require_same_files:
        extra_files = new_files - reviewed_files
        if extra_files:
            return False, f"New files touched: {', '.join(sorted(extra_files))}"

    # Count lines changed
    stat_output = run_git(["git", "diff", "--stat", reviewed_sha, head])
    total_changes = 0

    # Parse stat output: " 5 files changed, 10 insertions(+), 5 deletions(-)"
    insertions = re.search(r"(\d+)\s+insertions?", stat_output)
    deletions = re.search(r"(\d+)\s+deletions?", stat_output)

    total_changes += int(insertions.group(1)) if insertions else 0
    total_changes += int(deletions.group(1)) if deletions else 0

    if total_changes > max_changes:
        return False, f"Too many changes: {total_changes} lines (max {max_changes})"

    return True, ""


def main():
    config = load_config()

    if not config.get("enforce_review", True):
        sys.exit(0)

    command = get_command()
    if not command:
        sys.exit(0)

    lines = normalize_command(command)

    if not is_gh_pr_create(lines):
        sys.exit(0)

    if is_harmless_variant(lines):
        sys.exit(0)

    # Emergency bypass
    if config.get("allow_emergency_bypass", True) and os.environ.get("SKIP_PR_REVIEW") == "1":
        sys.exit(0)

    # Git worktree check
    if config.get("require_worktree", True) and not run_git(
        ["git", "rev-parse", "--is-inside-work-tree"]
    ):
        sys.exit(0)

    branch = run_git(["git", "branch", "--show-current"])
    head = run_git(["git", "rev-parse", "HEAD"])

    if not branch or not head:
        sys.exit(0)

    # Branch exclusions
    if branch in config.get("branch_exclusions", ["main", "master"]):
        sys.exit(0)

    safe_branch = branch.replace("/", "-")
    marker_path = Path(tempfile.gettempdir()) / f"pr-diff-review-{safe_branch}.done"

    reason = ""
    allow = False

    if not marker_path.exists():
        reason = f"No pre-PR review has been run for branch '{branch}'."
    else:
        reviewed_sha = marker_path.read_text().strip()
        if reviewed_sha == head:
            allow = True
        else:
            # Try incremental mode
            allowed, inc_reason = check_incremental_mode(reviewed_sha, head, config)
            if allowed:
                # Update marker and allow
                marker_path.write_text(head)
                sys.exit(0)
            reason = (
                f"Review is stale: marker SHA {reviewed_sha[:8]} != HEAD {head[:8]}. "
                f"Commits were added after review. {inc_reason}"
            )

    if allow:
        sys.exit(0)

    # Block with helpful message
    response = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"BLOCKED: gh pr create requires pre-PR diff review.\n\n"
                f"{reason}\n\n"
                f"Run the subagent first:\n"
                f'  Agent(subagent_type="pr-diff-reviewer", description="pre-PR review", '
                f'prompt="Review diff for branch {branch} before PR creation. HEAD={head}")\n\n'
                f"The subagent will analyze git diff against main, apply the universal + "
                f"project checklist, and create /tmp/pr-diff-review-{safe_branch}.done "
                f"on success. Fix any blockers it reports, then retry gh pr create.\n\n"
            )
            + (
                "Emergency bypass: SKIP_PR_REVIEW=1 gh pr create ..."
                if config.get("allow_emergency_bypass", True)
                else ""
            ),
        }
    }

    print(json.dumps(response, indent=2))


if __name__ == "__main__":
    main()
