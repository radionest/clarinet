## Why

`uv run clarinet admin reset-password` refuses any target that is not a
superuser (`clarinet/utils/admin.py:33`), so an operator cannot reset a regular
user's password from the CLI — even though the API already permits exactly that
for admins (`PUT /api/user/{user_id}` via `UserService.update_user`). The check
reads as a security guard but protects nothing: reaching the CLI already
implies shell access to the server and its database.

## What Changes

- Drop the `is_superuser` guard from the password-reset helper so any existing
  user (matched by email) can be reset.
- Rename the helper `reset_admin_password` → `reset_user_password` (single
  caller: `clarinet/cli/main.py:1896`); semantics otherwise unchanged.
- Update the `reset-password` subcommand help text to say "user" instead of
  "admin".
- Behaviour: `uv run clarinet admin reset-password --username alice` works for
  ordinary users. Unknown email still returns `False` with an error log.

Deliberately out of scope (decided in review):

- No new endpoint — the API path already covers admin-driven resets.

Note: the original design (pre-#651) also excluded session revocation. On
current main, `UserRepository.update_password` — which this helper already
calls — revokes sessions atomically with the hash write (#651), matching the
API path. The change therefore inherits revocation instead of avoiding it;
see spec `Sessions are revoked on reset`.

## Capabilities

### New Capabilities
- `admin-password-reset`: operator CLI for resetting any local user's password
  (`clarinet admin reset-password`), target-matching, failure modes, and what
  the command deliberately does not do (session revocation).

### Modified Capabilities

(none — no existing spec covers CLI admin commands)

## Impact

- `clarinet/utils/admin.py` — guard removal + rename.
- `clarinet/cli/main.py` — import/call site (`reset-password` branch) + parser
  help text.
- New test file `tests/test_cli_password_reset.py` (pattern:
  `tests/test_cli_anon_migrate.py` — direct helper import, seeded user, mocked
  `db_manager`, no subprocess).
- No DB schema change, no migration, no API change.
