# Design — admin-reset-any-user

## Context

`reset_admin_password` (`clarinet/utils/admin.py:14`) resets a password by
email lookup but bails with `False` when the target is not a superuser
(`admin.py:33-35`). Its only caller is the `admin reset-password` branch of
`clarinet/cli/main.py:1893-1904`. The API counterpart
(`PUT /api/user/{user_id}` → `UserService.update_user`) has no such
restriction for admin callers, so the CLI is arbitrarily narrower than the
API.

## Goals / Non-Goals

**Goals**

- An operator can reset any existing user's password from the CLI.
- Failure mode for unknown email is preserved (error log + `False`).

**Non-Goals**

- No new API endpoint (API already covers admin-driven resets).
- No session revocation on reset (matches both current CLI helper and
  `update_user`).
- No renaming of the `admin` command group.

## Decisions

→ `openspec/decisions.md` register (D15).

## Risks / Trade-offs

- [Operator resets a password they did not mean to] → the command prompts for
  the new password twice (existing getpass confirm flow), and the helper logs
  the reset target at INFO.
- [Stale sessions survive a reset] → none: since #651 the shared
  `update_password` revokes sessions atomically with the hash write; this
  change inherits that behaviour (see spec).

## Migration Plan

Pure code change, no schema/API impact. Rollback = revert the commit.

## Open Questions

(none)
