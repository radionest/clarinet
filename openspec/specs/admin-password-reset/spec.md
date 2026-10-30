# admin-password-reset Specification

## Purpose

Password reset from the operator CLI (`clarinet admin reset-password`): who can
be reset (any existing user, matched by email — D15), what happens to the
target's sessions (revoked atomically with the hash write, inherited from
`UserRepository.update_password`, #651), and how the command surfaces itself
(stays in the `admin` group with `--username`).

## Requirements

### Requirement: Reset targets any existing user

The `clarinet admin reset-password` CLI helper SHALL reset the password of any
existing user matched by email, regardless of `is_superuser`. The helper MUST
NOT reject a target for being a non-superuser.

#### Scenario: Regular user password is reset

- **WHEN** the helper runs with the email of an existing non-superuser user
  and a new password
- **THEN** the helper returns `True`
- **AND** the stored `hashed_password` verifies against the new password via
  `verify_password`
- **AND** the stored `hashed_password` no longer verifies against the old
  password

#### Scenario: Superuser password is still reset

- **WHEN** the helper runs with the email of an existing superuser
- **THEN** the helper returns `True` and the new password verifies

### Requirement: Unknown target fails without side effects

The helper SHALL return `False` and log an error when no user matches the
given email, and MUST NOT write anything to the database in that case.

#### Scenario: Unknown email

- **WHEN** the helper runs with an email that matches no user
- **THEN** the helper returns `False`
- **AND** an error naming the email is logged

### Requirement: Sessions are revoked on reset

The password reset helper SHALL revoke all existing sessions of the target
user as part of the reset. This is inherited from
`UserRepository.update_password` (#651) — the same single-commit semantics as
the API path (`UserService.update_user`): a failed revoke leaves the old
password in place rather than a new password with old sessions still valid.

#### Scenario: Reset revokes the target's sessions

- **WHEN** a user with existing sessions has their password reset via the
  CLI helper
- **THEN** all of the user's `AccessToken` rows are deleted in the same
  commit that persists the new hash

### Requirement: Command stays in the admin group

The subcommand SHALL remain `clarinet admin reset-password` with the
`--username` argument (email of the target). Only the help text MUST change to
reflect that any user can be reset, not only admins.

#### Scenario: Help text names users, not only admins

- **WHEN** `clarinet admin reset-password --help` is rendered
- **THEN** the subcommand help and the `--username` help reference a "user"
  rather than an "admin"
