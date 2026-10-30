# unify-project-scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `clarinet init` work from a wheel by moving the scaffold payload inside the package, retire the template system, split the agent-doc payload into managed vs project-owned, and flip the custom-code root default to `./plan/`.

**Architecture:** The scaffold payload moves from top-level `examples/project_template/` to `clarinet/scaffold/`, resolved off `clarinet.__file__` and force-shipped via hatch `artifacts` — the mechanism `clarinet/docs` already uses. `clarinet init` becomes a single path that copies that payload then delegates agent docs to the existing `scaffold_agent_docs()`. Inside `agent_scaffold`, `overview.md` is reclassified from a managed rule to a write-once seed at `<project>/.claude/CLAUDE.md`.

**Tech Stack:** Python 3.12+, hatchling build backend, pytest, argparse CLI, uv.

## Global Constraints

- Reference artifacts: `openspec/changes/unify-project-scaffold/{design.md,specs/}`.
- All Python tooling runs through `uv run`.
- Logging: `from clarinet.utils.logger import logger`; f-strings in log calls; never `print()` for diagnostics (CLI operator output already uses `print` in `main.py` — match the surrounding file).
- `clarinet/docs/project-setup.md` and `.claude/rules/project-setup.md` must stay **byte-identical** (`test_deep_docs_identical_to_rules_seeds`).
- Task 1 is a hard gate on Task 5: the template `.claude/` tree is the only home of the harvested content.
- Alembic is project-level — this change ships no migration.
- Target version: `0.11.0` (breaking).
- Gate command: `./scripts/run_tests.sh -k "agent_scaffold or scaffold or config_loading or cli_init or templates" -q` — validated at **100 of 3329** collected pre-change; expect growth as tests are added.

---

## Task 1: Harvest the drifted documentation

**Files:**
- Modify: `clarinet/docs/agent/claude/definitions.md`
- Reference: `examples/project_template/.claude/rules/definitions.md`

**Interfaces:**
- Consumes: nothing.
- Produces: a packaged `definitions.md` containing `{parent_id}`, `allow_path_collision` and `unique_by`, which Task 5 relies on before deleting the template copy.

- [x] **Step 1: Confirm the harvest list is complete**

```bash
for f in anonymization definitions schemas scripting slicer utils workflows; do
  echo "### $f"
  diff -u "examples/project_template/.claude/rules/$f.md" "clarinet/docs/agent/claude/$f.md"
done > /tmp/harvest-diff.txt 2>&1
diff -u examples/project_template/.claude/CLAUDE.md clarinet/docs/agent/claude/overview.md >> /tmp/harvest-diff.txt 2>&1
```

Read `/tmp/harvest-diff.txt`. Expected: only `definitions.md` has content the packaged copy lacks (`-` lines that are not link-form changes). `anonymization`/`slicer`/`workflows` differ only by `<clarinet>/…` vs `{{CLARINET_DOCS}}/…`; `schemas` and `overview` are packaged-ahead; `scripting`/`utils` identical. If anything else shows content-only `-` lines, harvest it too and note it in the commit body.

- [x] **Step 2: Add the `{parent_id}` row to the path-pattern table**

In `clarinet/docs/agent/claude/definitions.md`, in the pattern table, after the `{origin_type}` row:

```markdown
| `{parent_id}` | `record.parent_record_id` (FK value) — the exact per-parent discriminator, resolved without loading the parent |
```

- [x] **Step 3: Add the `allow_path_collision` row to the `FileRef` field table**

After the `required` row:

```markdown
| `allow_path_collision` | `bool` (default `False`) | Opts this one binding out of output-path uniqueness validation (the author guarantees uniqueness, e.g. via `{data.FIELD}`) |
```

- [x] **Step 4: Replace the cardinality row with the `unique_by` pair**

Replace:

```markdown
| `min_records`, `max_records` | How many records of this type must/may exist per parent |
```

with:

```markdown
| `min_records`, `max_records` | How many records of this type must/may exist per level context (patient/study/series) |
| `unique_by` | Uniqueness partitions within the level context: set from `{"user", "parent"}` (default both), or `None` — no uniqueness. Legacy `unique_per_user=` is accepted, translated and deprecation-warned |
```

- [x] **Step 5: Verify the packaged payload guards still pass**

Run: `uv run pytest tests/test_agent_scaffold.py -q`
Expected: PASS (no `<clarinet>` string introduced, token links still resolve).

- [x] **Step 6: Commit**

```bash
git add clarinet/docs/agent/claude/definitions.md
git commit -m "docs(agent): backport unique_by, {parent_id} and allow_path_collision to the shipped definitions doc"
```

---

## Task 2: Move the scaffold payload into the package

**Files:**
- Create: `clarinet/scaffold/` (moved content), `clarinet/utils/project_scaffold.py`
- Modify: `pyproject.toml:113-117`
- Test: `tests/test_project_scaffold.py`

**Interfaces:**
- Produces: `scaffold_source_dir() -> Path` and `SCAFFOLD_DOTFILES: dict[str, str]` in `clarinet/utils/project_scaffold.py`. Task 4 consumes both.

- [x] **Step 1: Write the failing test**

Create `tests/test_project_scaffold.py`:

```python
"""Unit tests for the packaged project scaffold payload."""

from pathlib import Path

import clarinet
from clarinet.utils.project_scaffold import SCAFFOLD_DOTFILES, scaffold_source_dir

PACKAGE_ROOT = Path(clarinet.__file__).resolve().parent

# Every file clarinet init writes, as a payload-relative path.
EXPECTED_PAYLOAD = [
    "settings.toml",
    "settings.custom.toml",
    "env.example",
    "gitignore",
    "plan/slicer_hydrators.py",
    "plan/definitions/record_types.py",
    "plan/workflows/pipeline_flow.py",
    "plan/validators/example_validator.py",
    "plan/schemas/_common.schema.json",
    "plan/schemas/first-check.schema.json",
    "plan/scripts/example.py",
    "plan/utils/__init__.py",
]


def test_scaffold_root_is_inside_the_package() -> None:
    """Regression guard for #472: the payload must ship with the wheel."""
    src = scaffold_source_dir()
    assert src.is_dir()
    assert PACKAGE_ROOT in src.parents or src.parent == PACKAGE_ROOT


def test_payload_manifest_complete() -> None:
    src = scaffold_source_dir()
    missing = [rel for rel in EXPECTED_PAYLOAD if not (src / rel).is_file()]
    assert not missing, f"payload files missing from the package: {missing}"


def test_payload_carries_no_dotfiles() -> None:
    """A .gitignore inside the package would govern what git — and the wheel — keeps."""
    assert not list(scaffold_source_dir().rglob(".gitignore"))


def test_payload_carries_no_agent_docs() -> None:
    assert not (scaffold_source_dir() / ".claude").exists()


def test_dotfile_map_targets_are_dotted() -> None:
    assert SCAFFOLD_DOTFILES == {"gitignore": ".gitignore", "env.example": ".env.example"}
```

- [x] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_project_scaffold.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'clarinet.utils.project_scaffold'`

- [x] **Step 3: Move the payload**

```bash
mkdir -p clarinet/scaffold
git mv examples/project_template/settings.toml clarinet/scaffold/settings.toml
git mv examples/project_template/settings.custom.toml clarinet/scaffold/settings.custom.toml
git mv examples/project_template/plan clarinet/scaffold/plan
git mv examples/project_template/.env.example clarinet/scaffold/env.example
git mv examples/project_template/.gitignore clarinet/scaffold/gitignore
```

- [x] **Step 4: Add the resolver module**

Create `clarinet/utils/project_scaffold.py`:

```python
"""Locate the packaged project-scaffold payload (`clarinet init`).

The payload lives inside the package so it ships in every wheel — resolved off
``clarinet.__file__`` like ``agent_scaffold._package_docs_dir``. Files whose
target name starts with a dot are stored undotted: a ``.gitignore`` inside the
package would govern what git tracks there, and therefore what the build
backend ships.
"""

from pathlib import Path

import clarinet
from clarinet.exceptions.domain import ProjectScaffoldError

# payload name → name written into the target project
SCAFFOLD_DOTFILES: dict[str, str] = {
    "gitignore": ".gitignore",
    "env.example": ".env.example",
}


def scaffold_source_dir() -> Path:
    """Absolute path of the shipped scaffold payload.

    Raises:
        ProjectScaffoldError: the payload is missing (e.g. a wheel built without
            ``clarinet/scaffold``).
    """
    src = Path(clarinet.__file__).resolve().parent / "scaffold"
    if not src.is_dir():
        raise ProjectScaffoldError(f"project scaffold payload not found at {src}")
    return src
```

- [x] **Step 5: Add the exception**

In `clarinet/exceptions/domain.py`, directly after `AgentScaffoldError` (line 416-418), add its sibling. The base is verified: `AgentScaffoldError.__mro__` is `AgentScaffoldError → ClarinetError → Exception`.

```python
class ProjectScaffoldError(ClarinetError):
    """Raised when the packaged project-scaffold payload is missing or unusable
    (e.g. a wheel built without ``clarinet/scaffold``)."""
```

- [x] **Step 6: Force-ship the payload**

In `pyproject.toml`, extend the `[tool.hatch.build]` artifacts list:

```toml
[tool.hatch.build]
artifacts = [
    "clarinet/static/**/*",
    "clarinet/docs/**/*",
    "clarinet/scaffold/**/*",
]
```

- [x] **Step 7: Verify the payload is tracked and not ignored**

```bash
git status --short clarinet/scaffold > /tmp/scaffold-status.txt 2>&1
git check-ignore -v $(git ls-files -co --exclude-standard clarinet/scaffold) >> /tmp/scaffold-status.txt 2>&1
echo "check-ignore exit=$?" >> /tmp/scaffold-status.txt
```

Read the file. Expected: payload files staged as renames; `check-ignore` matches nothing (exit 1).

- [x] **Step 8: Run the test to verify it passes**

Run: `uv run pytest tests/test_project_scaffold.py -q`
Expected: PASS (5 tests)

- [x] **Step 9: Commit**

```bash
git add clarinet/scaffold clarinet/utils/project_scaffold.py clarinet/exceptions/domain.py pyproject.toml tests/test_project_scaffold.py examples/project_template
git commit -m "feat(cli): move the project scaffold payload inside the package"
```

---

## Task 3: Split managed docs from the project-owned seed

**Files:**
- Modify: `clarinet/utils/agent_scaffold.py:19-21,66-104`
- Test: `tests/test_agent_scaffold.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `scaffold_agent_docs(agent, *, project_dir, mode, force) -> Path` keeps its signature; it now also writes `<project_dir>/.claude/CLAUDE.md`. Task 4 calls it unchanged.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_agent_scaffold.py`:

```python
SEED_DOC = "overview"


def test_seed_written_to_project_claude_md(tmp_path: Path) -> None:
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    seed = tmp_path / ".claude" / "CLAUDE.md"
    assert seed.is_file()
    assert "managed by clarinet" not in seed.read_text(encoding="utf-8")
    assert not (tmp_path / MANAGED / f"{SEED_DOC}.md").exists()


def test_seed_not_overwritten_on_init(tmp_path: Path) -> None:
    seed = tmp_path / ".claude" / "CLAUDE.md"
    seed.parent.mkdir(parents=True)
    seed.write_text("my study", encoding="utf-8")
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    assert seed.read_text(encoding="utf-8") == "my study"


def test_seed_survives_update(tmp_path: Path) -> None:
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    seed = tmp_path / ".claude" / "CLAUDE.md"
    seed.write_text("my edited study", encoding="utf-8")
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")
    assert seed.read_text(encoding="utf-8") == "my edited study"


def test_update_prunes_dropped_managed_doc(tmp_path: Path) -> None:
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    stale = dest / "retired.md"
    stale.write_text(
        "<!-- managed by clarinet v0.0.0 — do not edit; run 'clarinet agent update' -->\nold\n",
        encoding="utf-8",
    )
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")
    assert not stale.exists()


def test_update_keeps_unmanaged_file(tmp_path: Path) -> None:
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    mine = dest / "mine.md"
    mine.write_text("hand-written\n", encoding="utf-8")
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")
    assert mine.read_text(encoding="utf-8") == "hand-written\n"


def test_update_migrates_legacy_managed_overview(tmp_path: Path) -> None:
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    legacy = dest / "overview.md"
    legacy.write_text(
        "<!-- managed by clarinet v0.10.20 — do not edit; run 'clarinet agent update' -->\n"
        "# My Study\nedited by the user\n",
        encoding="utf-8",
    )
    (tmp_path / ".claude" / "CLAUDE.md").unlink()
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")
    seed = tmp_path / ".claude" / "CLAUDE.md"
    assert not legacy.exists()
    assert "edited by the user" in seed.read_text(encoding="utf-8")
    assert "managed by clarinet" not in seed.read_text(encoding="utf-8")


def test_migration_does_not_clobber_existing_seed(tmp_path: Path) -> None:
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    (dest / "overview.md").write_text(
        "<!-- managed by clarinet v0.10.20 — do not edit; run 'clarinet agent update' -->\nlegacy\n",
        encoding="utf-8",
    )
    seed = tmp_path / ".claude" / "CLAUDE.md"
    seed.write_text("mine", encoding="utf-8")
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")
    assert seed.read_text(encoding="utf-8") == "mine"
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_agent_scaffold.py -q -k "seed or prunes or migrat or unmanaged"`
Expected: FAIL — the seed is still written into the managed dir as `overview.md`.

- [x] **Step 3: Add the partition and header constants**

In `clarinet/utils/agent_scaffold.py`, below `_DOCS_TOKEN`:

```python
# Payload docs owned by the project, not the framework: written once to the
# target below, never rewritten by `update`. overview.md's own body tells the
# user to replace its contents, so it cannot live in the managed dir.
SEED_DOCS: dict[str, str] = {"overview.md": "CLAUDE.md"}

_MANAGED_MARKER = "<!-- managed by clarinet"
```

- [x] **Step 4: Write the seed during install**

In `scaffold_agent_docs`, after the managed-doc write loop and before `return dest`:

```python
    for payload_name, target_name in SEED_DOCS.items():
        target = project_dir / ".claude" / target_name
        if target.exists():
            logger.info(f"Kept existing {target}")
            continue
        text = (src / payload_name).read_text(encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.replace(_DOCS_TOKEN, docs_root.as_posix()), encoding="utf-8")
        logger.info(f"Wrote {target}")
```

Then exclude seeds from the managed loop by changing it to skip them:

```python
    for md in sorted(src.glob("*.md")):
        if md.name in SEED_DOCS:
            continue
```

- [x] **Step 5: Prune and migrate on update**

Still in `scaffold_agent_docs`, after the seed block, guarded by `mode == "update"`:

```python
    if mode == "update":
        payload_names = {p.name for p in src.glob("*.md")} - set(SEED_DOCS)
        for stale in sorted(dest.glob("*.md")):
            if stale.name in payload_names:
                continue
            text = stale.read_text(encoding="utf-8")
            if not text.startswith(_MANAGED_MARKER):
                continue  # project-owned file living in the managed dir
            target_name = SEED_DOCS.get(stale.name)
            target = project_dir / ".claude" / target_name if target_name else None
            if target is not None and not target.exists():
                # One-release migration: a managed overview the user may have
                # edited becomes the project-owned seed instead of being deleted.
                body = text.split("\n", 1)[1] if "\n" in text else ""
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
                logger.info(f"Migrated {stale} → {target}")
            else:
                logger.info(f"Removed stale managed doc {stale}")
            stale.unlink()
```

- [x] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_scaffold.py -q`
Expected: PASS. `test_project_template_ships_same_section_rules` still passes here — it is deleted in Task 5.

- [x] **Step 7: Commit**

```bash
git add clarinet/utils/agent_scaffold.py tests/test_agent_scaffold.py
git commit -m "feat(agent): make overview.md a project-owned seed and prune stale managed docs"
```

---

## Task 4: Rewire `clarinet init` to a single path

**Files:**
- Modify: `clarinet/cli/main.py:27-49` (`init_project`), `:1424-1440` (argparse), `:1838-1844` (dispatch)
- Test: `tests/test_cli_init.py`

**Interfaces:**
- Consumes: `scaffold_source_dir()`, `SCAFFOLD_DOTFILES` (Task 2); `scaffold_agent_docs()` (Task 3).
- Produces: `init_project(path: str) -> None` — no `template` parameter.

- [x] **Step 1: Write the failing tests**

Create `tests/test_cli_init.py`:

```python
"""Unit tests for `clarinet init`."""

from pathlib import Path

import pytest

from clarinet.cli.main import init_project


def test_init_produces_plan_layout(tmp_path: Path) -> None:
    init_project(str(tmp_path))
    assert (tmp_path / "plan" / "definitions" / "record_types.py").is_file()
    assert (tmp_path / "plan" / "workflows" / "pipeline_flow.py").is_file()
    assert not (tmp_path / "tasks").exists()


def test_init_materializes_dotfiles(tmp_path: Path) -> None:
    init_project(str(tmp_path))
    assert (tmp_path / ".gitignore").is_file()
    assert (tmp_path / ".env.example").is_file()
    assert not (tmp_path / "gitignore").exists()
    assert not (tmp_path / "env.example").exists()


def test_init_installs_agent_docs(tmp_path: Path) -> None:
    init_project(str(tmp_path))
    assert (tmp_path / ".claude" / "CLAUDE.md").is_file()
    assert (tmp_path / ".claude" / "rules" / "clarinet" / "definitions.md").is_file()


def test_init_never_overwrites(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text("project_name = 'mine'\n", encoding="utf-8")
    init_project(str(tmp_path))
    assert settings_file.read_text(encoding="utf-8") == "project_name = 'mine'\n"
    assert (tmp_path / "plan").is_dir()


def test_init_signature_has_no_template_param() -> None:
    import inspect

    assert list(inspect.signature(init_project).parameters) == ["path"]


def test_templates_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("clarinet.cli.templates")
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_cli_init.py -q`
Expected: FAIL — `init_project` still creates `tasks/` and accepts `template`.

- [x] **Step 3: Rewrite `init_project`**

Replace `clarinet/cli/main.py:27-49` (the signature, the template branch, the `directories` loop) with:

```python
def init_project(path: str) -> None:
    """Scaffold a new Clarinet project at *path*.

    Copies the packaged scaffold payload (never overwriting an existing file),
    materializes the undotted payload dotfiles, then installs the framework
    agent docs through the same mechanism `clarinet agent init` uses.
    """
    from clarinet.utils.agent_scaffold import scaffold_agent_docs
    from clarinet.utils.project_scaffold import SCAFFOLD_DOTFILES, scaffold_source_dir

    project_path = Path(path).resolve()
    src = scaffold_source_dir()

    skipped: list[str] = []
    for item in sorted(src.rglob("*")):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        target_rel = Path(SCAFFOLD_DOTFILES.get(str(rel), str(rel)))
        target = project_path / target_rel
        if target.exists():
            skipped.append(str(target_rel))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)

    scaffold_agent_docs("claude", project_dir=project_path, mode="init")

    if skipped:
        logger.info(f"Kept existing files: {', '.join(skipped)}")
    logger.info(f"Project initialized at {project_path}")
    logger.info(
        "A full worked example lives at examples/demo/ in the clarinet repository "
        "(reading material, not a scaffold)."
    )
```

The `SCAFFOLD_DOTFILES` lookup uses `str(rel)` because only top-level payload
files are dotted; nested paths never match and pass through unchanged.

- [x] **Step 4: Drop the template flags from argparse**

At `clarinet/cli/main.py:1431-1440`, delete both `init_parser.add_argument` blocks for `--template`/`-t` and `--list-templates`, leaving only the `path` argument.

- [x] **Step 5: Simplify the dispatch**

Replace `clarinet/cli/main.py:1838-1844`:

```python
    if args.command == "init":
        init_project(args.path)
```

- [x] **Step 6: Delete the templates module**

```bash
git rm clarinet/cli/templates.py
```

- [x] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli_init.py -q`
Expected: PASS (6 tests)

- [x] **Step 8: Commit**

```bash
git add clarinet/cli/main.py tests/test_cli_init.py
git commit -m "feat(cli)!: single-path clarinet init, retire --template"
```

---

## Task 5: Remove the superseded template tree

**Files:**
- Delete: `examples/project_template/.claude/`, `examples/project_template/`
- Modify: `tests/test_agent_scaffold.py:21-22,52-63`

**Interfaces:**
- Consumes: Task 1's harvested content (hard gate).

- [x] **Step 1: Verify Task 1 landed**

```bash
grep -c "unique_by\|parent_id\|allow_path_collision" clarinet/docs/agent/claude/definitions.md  # bash-ok
```

Expected: `3` or more. **If this prints 0, stop** — Task 1 has not landed and deleting the template copy would destroy the only downstream-facing home of that content.

- [x] **Step 2: Delete the template tree**

```bash
git rm -r examples/project_template
```

- [x] **Step 3: Remove the orphaned test and its constants**

In `tests/test_agent_scaffold.py`, delete `test_project_template_ships_same_section_rules` (lines 52-63) and the two constants it used (lines 21-22):

```python
PROJECT_TEMPLATE_RULES = REPO_ROOT / "examples" / "project_template" / ".claude" / "rules"
PROJECT_TEMPLATE_CLAUDE_MD = REPO_ROOT / "examples" / "project_template" / ".claude" / "CLAUDE.md"
```

- [x] **Step 4: Run to verify nothing else referenced them**

Run: `uv run pytest tests/test_agent_scaffold.py tests/test_project_scaffold.py -q`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add -A examples tests/test_agent_scaffold.py
git commit -m "refactor: drop the duplicated agent-doc copy from the project template"
```

---

## Task 6: Flip the custom-code root to `plan/`

**Files:**
- Modify: `clarinet/settings.py:390`, `clarinet/config/python_loader.py:317-320`, `clarinet/config/CLAUDE.md`
- Modify: `examples/demo/settings.toml:29`, `README.md:129,139`
- Test: `tests/test_config_loading.py`

**Interfaces:**
- Produces: `settings.config_tasks_path` defaults to `"./plan/"`; `load_python_config` raises `ConfigLoadError` on a missing root.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_config_loading.py`:

```python
@pytest.mark.asyncio
async def test_missing_plan_root_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing root must abort, not reconcile zero record types (#472 follow-on)."""
    from clarinet.config.python_loader import load_python_config

    missing = tmp_path / "plan"
    with pytest.raises(ConfigLoadError, match="does not exist"):
        await load_python_config(missing)


@pytest.mark.asyncio
async def test_empty_plan_root_still_warns(tmp_path: Path) -> None:
    """An existing but empty root keeps the lenient behavior."""
    from clarinet.config.python_loader import load_python_config

    (tmp_path / "plan").mkdir()
    assert await load_python_config(tmp_path / "plan") == []


@pytest.mark.asyncio
async def test_missing_default_root_names_the_flip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clarinet.config.python_loader import load_python_config
    from clarinet.settings import settings

    monkeypatch.setattr(settings, "config_tasks_path", "./plan/")
    with pytest.raises(ConfigLoadError, match="tasks/"):
        await load_python_config(tmp_path / "plan")


def test_config_tasks_path_default_is_plan() -> None:
    from clarinet.settings import Settings

    assert Settings.model_fields["config_tasks_path"].default == "./plan/"
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_config_loading.py -q -k "plan_root or the_flip or default_is_plan"`
Expected: FAIL — the loader returns `[]` and the default is still `./tasks/`.

- [x] **Step 3: Flip the default**

`clarinet/settings.py:390`:

```python
    config_tasks_path: str = "./plan/"
```

- [x] **Step 4: Abort on a missing root**

In `clarinet/config/python_loader.py`, insert **after** the function's local imports (lines 314-315, which already bind `settings`) and **before** the existing `record_types_file` check at line 317:

```python
    if not folder.is_dir():
        from clarinet.settings import Settings

        # verified: Settings.model_fields[...].default is readable at runtime
        default = Settings.model_fields["config_tasks_path"].default
        hint = ""
        if settings.config_tasks_path == default:
            hint = (
                " This path came from the default, which changed from './tasks/' to "
                "'./plan/' in clarinet 0.11.0. If your project uses the old layout, "
                "set config_tasks_path = \"./tasks/\" in settings.toml."
            )
        raise ConfigLoadError(f"config root '{folder}' does not exist.{hint}")
```

Keep the existing `record_types_file.is_file()` warning branch below it untouched — an existing-but-empty root stays lenient.

- [x] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config_loading.py -q`
Expected: PASS

- [x] **Step 6: Move the demo to the new layout**

```bash
git mv examples/demo/tasks examples/demo/plan
```

In `examples/demo/settings.toml:29`:

```toml
recordflow_paths = ["./plan/workflows"]
```

- [x] **Step 7: Update the docs that name the old paths**

- `README.md:129` and `README.md:139` — `examples/demo/tasks/...` → `examples/demo/plan/...`
- `clarinet/config/CLAUDE.md` — the `config_tasks_path: str = "./tasks/"` line in the Settings block, and the two `tasks/` folder-structure listings under "Python Loader", become `plan/`.

- [x] **Step 8: Commit**

```bash
git add -A clarinet/settings.py clarinet/config examples/demo README.md tests/test_config_loading.py
git commit -m "feat(config)!: default config_tasks_path to ./plan/ and abort on a missing root"
```

---

## Task 7: Documentation, changelog, release

**Files:**
- Modify: `clarinet/docs/project-setup.md`, `.claude/rules/project-setup.md`, `CHANGELOG.md`, `pyproject.toml`

- [x] **Step 1: Rewrite the packaged setup doc**

In `clarinet/docs/project-setup.md`, replace the "Creating a Project" block (the four `clarinet init` variants) and the paragraph beginning "Templates are copied from `examples/`" with:

```markdown
## Creating a Project

```bash
clarinet init my_project      # Full research scaffold (settings, plan/, agent docs)
```

`clarinet init` writes the project skeleton from the payload shipped inside the
clarinet package, then installs the framework agent docs — the same set
`clarinet agent init` delivers. Existing files are never overwritten.

For an existing project, run `clarinet agent init` to install the agent docs on
their own (re-run `clarinet agent update` after upgrading clarinet to refresh
them). A full worked example lives at `examples/demo/` in the clarinet
repository — reading material, not a scaffold.
```

Also update the `config_tasks_path` mention so it reflects `./plan/` as the default rather than an explicit setting.

- [x] **Step 2: Mirror it byte-identically**

```bash
cp clarinet/docs/project-setup.md .claude/rules/project-setup.md
```

- [x] **Step 3: Verify the golden guard**

Run: `uv run pytest tests/test_agent_scaffold.py -q -k "deep_docs or token_links or repo_links"`
Expected: PASS

- [x] **Step 4: Add the changelog entry**

At the top of `CHANGELOG.md`, following the file's existing heading style:

```markdown
## 0.11.0

### Breaking

- `clarinet init` no longer accepts `--template` / `--list-templates`. It always
  scaffolds the full project skeleton, now shipped inside the package — fixing
  `init --template` being unusable from any pip install (#472).
- `config_tasks_path` now defaults to `./plan/` (was `./tasks/`). A project that
  omits the setting and keeps a `tasks/` directory must add
  `config_tasks_path = "./tasks/"` to `settings.toml`, or rename the directory
  to `plan/`.
- A custom-code root that does not exist now aborts startup instead of logging a
  warning and reconciling zero record types.

### Changed

- `overview.md` is delivered as a project-owned `.claude/CLAUDE.md` seed rather
  than a managed rule: it is written once and never overwritten. Run
  `clarinet agent update` once after upgrading — an edited managed `overview.md`
  is migrated to `.claude/CLAUDE.md` rather than deleted.
- `clarinet agent update` removes managed docs that the installed version no
  longer ships.
```

- [x] **Step 5: Bump the version**

In `pyproject.toml`, set `version = "0.11.0"`.

- [x] **Step 6: Full gate**

```bash
./scripts/run_tests.sh -k "agent_scaffold or scaffold or config_loading or cli_init or templates" -q > /tmp/test-unify-scaffold.txt 2>&1
```

Read the file. Expected: PASS, collected count above the pre-change baseline of 100 (the `templates` term now matches nothing; the new `cli_init` and `project_scaffold` tests more than replace it).

- [x] **Step 7: `make check`**

Run: `make check`
Expected: clean. Re-`Read` any file before further edits — `ruff format` may have rewritten it.

- [x] **Step 8: Commit**

```bash
git add clarinet/docs/project-setup.md .claude/rules/project-setup.md CHANGELOG.md pyproject.toml
git commit -m "docs: document the single-path init and the ./plan/ default; bump to 0.11.0"
```

---

## Deferred

- **Project-root `scripts/` in the scaffold** — the packaged `overview.md`
  already documents operational scripts living there using the
  `clarinet.scripting` frame, but that frame belongs to the in-progress
  `add-scripting-frame` change. Resolve before Task 2 if the scaffold should
  create the directory; otherwise it stays out and `add-scripting-frame` adds it.
- **`examples/demo/.claude/settings.local.json` in the sdist** — a per-developer
  file in a published artifact. Unrelated to #472; pick up only if the packaging
  diff is already being reviewed.
