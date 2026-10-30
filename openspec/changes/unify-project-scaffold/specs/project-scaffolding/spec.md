# Spec Delta: project-scaffolding

## ADDED Requirements

### Requirement: Scaffold payload resides inside the installed package

The project scaffold payload SHALL be stored inside the `clarinet` package and
resolved relative to the package directory itself, so that it is present in any
installed distribution. Resolution MUST NOT depend on any path outside the
package (in particular, MUST NOT walk up to the package's parent directory).

#### Scenario: Resolving the scaffold root from an installed wheel

- **WHEN** the scaffold root is resolved in an environment where clarinet was installed from a wheel
- **THEN** the resolved path exists and is a descendant of the directory containing `clarinet/__init__.py`

#### Scenario: Payload is complete

- **WHEN** the scaffold payload is enumerated
- **THEN** every file the scaffold writes into a new project is present in the package payload directory

#### Scenario: No reliance on the source checkout

- **WHEN** the scaffold root is resolved
- **THEN** the resolved path is not derived from the repository's top-level `examples/` directory

---

### Requirement: A single `clarinet init` command scaffolds a project

`clarinet init [path]` SHALL always produce the complete project skeleton. The
CLI MUST NOT expose template selection or template listing options.

#### Scenario: Scaffolding a new project

- **WHEN** `clarinet init <path>` is run against an empty or non-existent directory
- **THEN** the directory contains the settings files, the `plan/` tree, and the agent documentation, and the command exits successfully

#### Scenario: Template selection is not offered

- **WHEN** the `init` command's options are inspected
- **THEN** no `--template` or `--list-templates` option is accepted

#### Scenario: Worked example remains discoverable

- **WHEN** `clarinet init` completes successfully
- **THEN** its output points the user at the repository's worked example as reading material

---

### Requirement: Scaffolded projects use the `plan/` layout

The scaffold SHALL produce a `plan/` directory as the project's custom-code root
and MUST NOT create a `tasks/` directory.

#### Scenario: Layout of a freshly scaffolded project

- **WHEN** a project is scaffolded
- **THEN** a `plan/` directory exists containing the definitions, workflows, validators, schemas, scripts and utils subtrees, and no `tasks/` directory is present

#### Scenario: Settings agree with the produced layout

- **WHEN** the scaffolded `settings.toml` is read
- **THEN** its custom-code root and RecordFlow paths refer to `plan/`

---

### Requirement: Dotfiles are stored undotted and materialized at scaffold time

Payload files whose target names begin with a dot SHALL be stored in the package
without the leading dot and written to their dotted names when scaffolding. This
prevents an in-package ignore file from governing which payload files version
control — and therefore the built distribution — retains.

#### Scenario: Ignore file is materialized

- **WHEN** a project is scaffolded
- **THEN** the project contains a `.gitignore` whose content matches the undotted payload file

#### Scenario: Environment example is materialized

- **WHEN** a project is scaffolded
- **THEN** the project contains a `.env.example` whose content matches the undotted payload file

#### Scenario: Package payload carries no ignore file

- **WHEN** the package payload directory is enumerated
- **THEN** it contains no file named `.gitignore`

---

### Requirement: Scaffolding never overwrites existing files

The scaffold SHALL skip any target file that already exists, and MUST report
which files were skipped. Existing project content MUST NOT be destroyed by
running `init`.

#### Scenario: Re-running init over an existing project

- **WHEN** `clarinet init <path>` is run against a directory that already contains a settings file
- **THEN** the existing file retains its original content and the command reports it as skipped

#### Scenario: Partial scaffold is completed

- **WHEN** `clarinet init <path>` is run against a directory containing some but not all scaffold files
- **THEN** the missing files are created and the pre-existing ones are left untouched

---

### Requirement: Agent documentation is delegated to the agent-doc scaffolder

`clarinet init` SHALL install agent documentation by invoking the agent-doc
delivery mechanism rather than by copying documentation from its own payload.
The scaffold payload MUST NOT contain a copy of the agent documentation.

#### Scenario: Agent docs installed during init

- **WHEN** a project is scaffolded
- **THEN** the managed agent rules and the project overview are present, having been produced by the agent-doc scaffolder

#### Scenario: No duplicate documentation in the payload

- **WHEN** the scaffold payload directory is enumerated
- **THEN** it contains no agent rule documents
