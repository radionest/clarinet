# Tasks — admin-reset-any-user

## 1. Helper change

- [x] 1.1 In `clarinet/utils/admin.py`: drop the `is_superuser` guard from
  the password-reset helper and rename `reset_admin_password` →
  `reset_user_password`; update docstring to "user" (drop "admin"). Keep the
  `UserRepository.update_password` write as-is. Verify: `uv run python -c
  "from clarinet.utils.admin import reset_user_password"` imports cleanly.
- [x] 1.2 In `clarinet/cli/main.py` reset-password branch (search
  `reset_admin_password`): import and call `reset_user_password`; update
  parser help for the `reset-password` subcommand and its `--username`
  argument from "admin" wording to "user". Verify: `uv run clarinet admin
  reset-password --help` shows the new text.

## 2. Tests

- [x] 2.1 Create `tests/test_cli_password_reset.py` following the
  `tests/test_cli_anon_migrate.py` pattern (direct helper import, seeded
  user, `patch("clarinet.utils.admin.db_manager")` with
  `PassThroughSession(test_session)`, no subprocess). Cover:
  (a) non-superuser password reset returns True and the new password verifies
  via `verify_password` (old one no longer does), (b) unknown email returns
  False, (c) superuser reset still works, (d) reset deletes the user's
  `AccessToken` rows (session revocation, #651). Verify:
  `./scripts/run_tests.sh tests/test_cli_password_reset.py -q`.

## 3. Checkpoint

- [x] 3.1 Run `make check` (format + lint + typecheck) and the new test file
  together; all green.

