# Spec Delta: plan-directory-convention

## ADDED Requirements

### Requirement: The custom-code root defaults to `./plan/`

When a project does not set `config_tasks_path`, the framework SHALL resolve the
custom-code root to `./plan/`. This aligns the default with the layout produced
by the scaffold and described throughout the documentation.

#### Scenario: Project omitting the setting

- **WHEN** a project with a `plan/` directory and no `config_tasks_path` setting starts
- **THEN** its custom code is loaded from `plan/`

#### Scenario: Explicit setting still wins

- **WHEN** a project sets `config_tasks_path` to a directory other than `./plan/`
- **THEN** the custom-code root resolves to the configured directory

---

### Requirement: A missing custom-code root aborts startup instead of degrading silently

When the configured or defaulted custom-code root does not exist, the framework
SHALL abort startup. It MUST NOT continue with zero loaded record types, because
a project whose definitions silently fail to load keeps serving its previously
reconciled database rows and drifts from its configuration undetected.

#### Scenario: Missing root aborts

- **WHEN** the custom-code root does not exist and config loading runs
- **THEN** startup fails and no reconciliation against an empty definition set occurs

#### Scenario: Existing but empty root still warns rather than aborts

- **WHEN** the custom-code root exists but contains no record-type definitions
- **THEN** the existing warning behavior is retained and startup continues

---

### Requirement: The abort diagnostic is upgrade-aware

The failure raised for a missing custom-code root SHALL name the resolved path.
When the value was not set by the project and therefore came from the default,
the message MUST additionally state that the default changed from `./tasks/` to
`./plan/` and give the setting that restores the previous layout.

#### Scenario: Project relying on the previous default

- **WHEN** a project that has a `tasks/` directory and no `config_tasks_path` setting starts
- **THEN** startup fails with a message naming the unresolved `plan/` path, the change of default, and the setting to add in order to keep the previous layout

#### Scenario: Misconfigured explicit path

- **WHEN** a project sets `config_tasks_path` to a directory that does not exist
- **THEN** startup fails with a message naming the unresolved path, without claiming the default changed
