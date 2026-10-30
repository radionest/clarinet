# Path-Traversal Guards for Rendered File Paths — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make it impossible for a rendered `FileDefinition.pattern` to resolve to a path outside the record's working directory, at every sink that renders or joins one.

**Architecture:** Two layers. A *value guard* rejects `/`, `\`, NUL and `.`/`..` in each substituted value at the single substitution point inside `render_template`, reached through a new opt-in `path_safe=True` keyword. A *containment check*, `join_within`, then validates the joined path lexically — `os.path.normpath` plus a prefix comparison, no syscalls, so `Files.resolve` stays synchronous and cheap inside the `build_slicer_context` loop. A third, config-time validator refuses a pattern that is unsafe before any value is substituted, and — temporarily, tracked by #552 — refuses `{data.*}` placeholders outright.

**Tech Stack:** Python 3.12+, Pydantic v2 / SQLModel, pytest, `uv` for every tool invocation.

## Global Constraints

- Every Python tool runs through `uv`: `uv run <command>`. Never invoke bare `pytest`/`ruff`/`mypy`.
- `clarinet/files/_template.py` is a leaf module imported lazily by `clarinet.settings` (`settings.py:191`). It may import `clarinet.exceptions.domain` — that module is stdlib-only — but **nothing else** from `clarinet`. Adding a models/services import there creates a bootstrap cycle.
- Never import loguru directly: `from clarinet.utils.logger import logger`.
- Custom exceptions come from `clarinet.exceptions.http` (`UNPROCESSABLE_ENTITY`, …) at the API boundary and `clarinet.exceptions.domain` in the service//files layers.
- Never log a value drawn from `record.data` — it may carry PHI (`.claude/rules/logging-pii.md`).
- **AMENDED 2026-08-10 (human ruling, supersedes the code blocks below wherever they conflict):** the offending value must never appear in an `UnsafePathError` *message* either. `UnsafePathError.__init__` takes `(message, *, value=None)`, stores `self.value`, and exposes `metadata()`; messages name the placeholder key and the reason only. Reason: `api/exception_handlers.py:376` logs a `ConfigurationError` with `logger.opt(exception=exc)`, `utils/logger.py:107,152` render the traceback into the JSON and Loki sinks, and `scrub_sensitive` redacts only credential-shaped text — so a value in the message reaches persistent logs. Mirrors `InvalidPatientIdentifierError` (`exceptions/domain.py:172-190`), which already solves this.
- Conventional-commit messages. No `Co-Authored-By` trailers.
- Work happens in a git worktree, not on `main` (`require-worktree.sh` blocks edits to `clarinet/`, `tests/`, `examples/` on `main`).
- Run `uv run ruff format` before committing; `make check` must be clean before the PR.

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `clarinet/exceptions/domain.py` | `UnsafePathError`, sibling of `AnonPathError` | Modify |
| `clarinet/files/_template.py` | The three primitives + `path_safe` on `render_template` | Modify |
| `clarinet/files/__init__.py` | Lazy re-export so callers never import the private leaf | Modify |
| `clarinet/files/facade.py` | Wire `path_safe=True` / `join_within` into the render→join sites | Modify |
| `clarinet/services/pipeline/context.py` | Containment on the persisted-filename join | Modify |
| `clarinet/models/file_schema.py` | `pattern` validator on `FileDefinitionRead` | Modify |
| `clarinet/services/record_service.py` | Translate to 422 on record submit | Modify |
| `tests/test_path_safety.py` | Unit tests for the three primitives | Create |
| `tests/test_files_facade.py`, `tests/test_pipeline_context.py`, `tests/test_config_primitives.py`, `tests/test_file_registry_resolver.py`, `tests/test_file_validation.py`, `tests/test_path_uniqueness.py` | Integration + collateral | Modify |

### Baseline test counts (pinned 2026-08-10, `uv run pytest --collect-only -q`)

| File | Tests | File | Tests |
|---|---|---|---|
| `tests/test_files_facade.py` | 16 | `tests/test_file_validation.py` | 18 |
| `tests/test_path_template_renderer.py` | 52 | `tests/test_pipeline_context.py` | 69 |
| `tests/test_config_primitives.py` | 6 | `tests/test_storage_paths.py` | 56 |
| `tests/test_file_registry_resolver.py` | 20 | `tests/test_file_patterns.py` | 26 |
| `tests/test_path_uniqueness.py` | 10 | | |

The final gate selector

```
-k "path_safety or files_facade or file_registry_resolver or path_uniqueness or file_validation or path_template_renderer or pipeline_context"
```

collects **186** tests today (3227 in the suite). After this change it must collect **186 + the count of `tests/test_path_safety.py`**, and all must pass. A selector collecting 186 after the work is done means `test_path_safety.py` was not picked up — investigate rather than declaring green.

---

## Task 1: `UnsafePathError`

**Files:**
- Modify: `clarinet/exceptions/domain.py` (insert after `AnonPathError`, currently at line 467)
- Test: `tests/test_path_safety.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `clarinet.exceptions.domain.UnsafePathError`, subclass of `ConfigurationError`, raised by every primitive in Task 2.

- [ ] **Step 1: Write the failing test**

Create `tests/test_path_safety.py`:

```python
"""Tests for the path-safety primitives in ``clarinet.files._template``.

Pure unit tests, stdlib-only inputs, mirroring the style of
``tests/test_path_template_renderer.py``. These cover the value guard, the
lexical containment check, and the config-time pattern validator.
"""

from __future__ import annotations

import pytest

from clarinet.exceptions.domain import AnonPathError, ConfigurationError, UnsafePathError


class TestUnsafePathErrorTaxonomy:
    def test_subclasses_configuration_error(self):
        assert issubclass(UnsafePathError, ConfigurationError)

    def test_is_not_an_anon_path_error(self):
        # Four sites catch AnonPathError and degrade: Files.for_reader retries
        # with a raw-UID fallback, dicomweb/cache.py and tasks/cache_dicomweb.py
        # log and skip, cli/anon.py counts a failure. A traversal must never
        # degrade into any of those.
        assert not issubclass(UnsafePathError, AnonPathError)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_path_safety.py -q`
Expected: FAIL — `ImportError: cannot import name 'UnsafePathError'`

- [ ] **Step 3: Add the exception**

In `clarinet/exceptions/domain.py`, directly after the `AnonPathError` class body:

```python
class UnsafePathError(ConfigurationError):
    """Raised when a rendered file path would escape its working directory.

    A deliberate *sibling* of ``AnonPathError``, not a subclass. Four sites
    catch ``AnonPathError`` and degrade — ``Files.for_reader`` retries with a
    raw-UID fallback, ``services/dicomweb/cache.py`` and
    ``services/pipeline/tasks/cache_dicomweb.py`` log and skip, and
    ``cli/anon.py`` counts a failure. If a traversal raised something those
    caught, the fallback would silently retry the poisoned path and the rest
    would record it as routine. A traversal must always propagate.
    """
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `uv run pytest tests/test_path_safety.py -q`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add clarinet/exceptions/domain.py tests/test_path_safety.py
git commit -m "feat(files): add UnsafePathError as a non-degrading sibling of AnonPathError"
```

---

## Task 2: `assert_path_safe_value`

**Files:**
- Modify: `clarinet/files/_template.py` (append after `coerce_field_value`, which ends at line 176)
- Test: `tests/test_path_safety.py`

**Interfaces:**
- Consumes: `UnsafePathError` from Task 1.
- Produces: `assert_path_safe_value(key: str, value: str) -> None` — raises `UnsafePathError`, returns `None` on success. Task 4 calls it from inside `render_template`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_path_safety.py`:

```python
from clarinet.files._template import assert_path_safe_value


class TestAssertPathSafeValue:
    @pytest.mark.parametrize(
        "value",
        [
            "/etc/passwd",          # absolute: pathlib would discard the base entirely
            "../../etc/passwd",     # classic relative traversal
            "..",                   # the split form, where the pattern supplies the "/"
            ".",                    # current-directory reference
            "a/b",                  # a coerced collection can produce this
            "back\\slash",          # POSIX ignores it; the analyst's Slicer may not
            "nul\x00byte",
        ],
    )
    def test_rejects(self, value):
        with pytest.raises(UnsafePathError):
            assert_path_safe_value("patient_id", value)

    @pytest.mark.parametrize(
        "value",
        ["CT_SR", "mask.seg.nrrd", "1.2.840.113619.2.55", "a.b.c", "-dash", "_under"],
    )
    def test_accepts(self, value):
        assert assert_path_safe_value("patient_id", value) is None

    def test_error_names_the_key_but_not_the_value(self):
        with pytest.raises(UnsafePathError) as exc:
            assert_path_safe_value("data.secret_mrn", "/etc/passwd")
        assert "data.secret_mrn" in str(exc.value)
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_path_safety.py -q`
Expected: FAIL — `ImportError: cannot import name 'assert_path_safe_value'`

- [ ] **Step 3: Implement**

Add the import at the top of `clarinet/files/_template.py`, beside the existing stdlib imports:

```python
from clarinet.exceptions.domain import UnsafePathError
```

> `clarinet.exceptions.domain` imports only `collections.abc`, `dataclasses`, `pathlib`, `typing` and `uuid`, so this keeps the leaf free of the package graph and the lazy import in `settings.py:191` stays cycle-free.

Then append after `coerce_field_value`:

```python
_UNSAFE_IN_VALUE = ("/", "\\", "\x00")


def assert_path_safe_value(key: str, value: str) -> None:
    """Reject a substituted value that could alter a path's structure.

    Runs on the *coerced* value, so a collection flattening to ``"a/b"`` is
    caught. Raises ``UnsafePathError`` naming *key* and the reason. The value
    travels as ``exc.value``, never in the message and never in a log — record
    data may carry PHI, and a message reaches the log sinks via the
    ``ConfigurationError`` handler's traceback.
    """
    for bad in _UNSAFE_IN_VALUE:
        if bad in value:
            raise UnsafePathError(
                f"placeholder {{{key}}} resolved to a value containing {bad!r}, "
                f"which would change the path structure",
                value=value,
            )
    if value in (".", ".."):
        raise UnsafePathError(
            f"placeholder {{{key}}} resolved to a bare directory reference",
            value=value,
        )
```

- [ ] **Step 4: Run and watch it pass**

Run: `uv run pytest tests/test_path_safety.py -q`
Expected: PASS, 19 tests (2 + 7 + 6 + 1 + the 3 parametrize ids already counted).

- [ ] **Step 5: Commit**

```bash
git add clarinet/files/_template.py tests/test_path_safety.py
git commit -m "feat(files): add assert_path_safe_value for substituted path values"
```

---

## Task 3: `join_within`

**Files:**
- Modify: `clarinet/files/_template.py`
- Test: `tests/test_path_safety.py`

**Interfaces:**
- Consumes: `UnsafePathError`.
- Produces: `join_within(base: Path, rendered: str) -> Path` — returns the joined path, raises `UnsafePathError`. Tasks 5 and 6 call it.

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path

from clarinet.files._template import join_within

BASE = Path("/data/storage/anon_1/study/series")


class TestJoinWithin:
    @pytest.mark.parametrize(
        "rendered",
        [
            "/etc/passwd",       # absolute absorbs the base
            "../sibling.nrrd",
            "../../etc/passwd",
            "sub/../../escape.nrrd",   # normalises out of the base
            "",                  # LENIENT rendered a whole-pattern placeholder away
            ".",                 # equals the base after normalisation
            ".ssh",              # dot-leading basename
            ".bashrc",
            "sub/.hidden",       # dot-leading basename in a subdirectory
        ],
    )
    def test_rejects(self, rendered):
        with pytest.raises(UnsafePathError):
            join_within(BASE, rendered)

    @pytest.mark.parametrize(
        "rendered",
        ["mask.nrrd", "mask.seg.nrrd", "seg_7.seg.nrrd", "1.2.840.113619/mask.nrrd"],
    )
    def test_accepts(self, rendered):
        result = join_within(BASE, rendered)
        assert result.is_relative_to(BASE)

    def test_performs_no_filesystem_access(self, monkeypatch):
        # Files.resolve is sync and looped over the whole registry by
        # build_slicer_context; a syscall here would be a latency regression.
        def explode(*args, **kwargs):
            raise AssertionError("join_within must not touch the filesystem")

        monkeypatch.setattr(Path, "resolve", explode)
        monkeypatch.setattr(Path, "exists", explode)
        assert join_within(BASE, "mask.nrrd") == BASE / "mask.nrrd"
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_path_safety.py -q -k JoinWithin`
Expected: FAIL — `ImportError: cannot import name 'join_within'`

- [ ] **Step 3: Implement**

Add `import os.path` and `from pathlib import Path` to the imports of `clarinet/files/_template.py` (it already imports `PurePosixPath`; add `Path` alongside), then append:

```python
def join_within(base: Path, rendered: str) -> Path:
    """Join *rendered* onto *base*, refusing anything that escapes it.

    Purely lexical — ``os.path.normpath`` plus a prefix comparison, no
    filesystem access — so ``Files.resolve`` stays synchronous and cheap inside
    the ``build_slicer_context`` loop. ``_filter_in_sandbox`` remains the
    symlink-aware second layer at the delete and serve sinks.

    Rejects a result outside *base*, equal to *base* (LENIENT can render a
    whole-pattern placeholder to ``""``), carrying a ``..`` component, or whose
    **basename** starts with a dot. Dot-checking is basename-level on purpose:
    ``mask.seg.nrrd`` is normal, and checking the whole string would break
    subdirectory patterns.
    """
    if not rendered or not rendered.strip():
        raise UnsafePathError(
            f"rendered name is empty; would resolve to the working dir {base}",
            value=rendered,
        )
    joined = Path(os.path.normpath(base / rendered))
    if not joined.is_relative_to(base):
        raise UnsafePathError(
            f"rendered name escapes the working dir {base}", value=rendered
        )
    if joined == base:
        raise UnsafePathError(
            f"rendered name resolves to the working dir {base} itself", value=rendered
        )
    relative = joined.relative_to(base)
    if ".." in relative.parts:
        raise UnsafePathError("rendered name contains a '..' component", value=rendered)
    if joined.name.startswith("."):
        raise UnsafePathError("rendered name has a dot-leading basename", value=rendered)
    return joined
```

> `os.path.normpath` collapses `..` before the containment test, so
> `sub/../../escape.nrrd` fails `is_relative_to` rather than slipping through
> the later `..` component check. That check is the backstop for a `..` that
> normalisation cannot collapse.

- [ ] **Step 4: Run and watch it pass**

Run: `uv run pytest tests/test_path_safety.py -q`
Expected: PASS, all tests including the 14 new ones.

- [ ] **Step 5: Commit**

```bash
git add clarinet/files/_template.py tests/test_path_safety.py
git commit -m "feat(files): add lexical join_within containment check"
```

---

## Task 4: `path_safe` on `render_template`

**Files:**
- Modify: `clarinet/files/_template.py:200-253` (`render_template` and its `_replace` closure)
- Test: `tests/test_path_safety.py`

**Interfaces:**
- Consumes: `assert_path_safe_value`.
- Produces: `render_template(..., path_safe: bool = False)`. Tasks 5 and 8 pass `path_safe=True`.

**The trap this task must avoid:** `_replace` already wraps `coerce_field_value` in `try/except ValueError`, and in LENIENT mode it *swallows* the error and substitutes `missing`. If the guard raised inside that block — or raised a `ValueError` — every violation would be silently rewritten to `""` and the guard would be worse than useless. `UnsafePathError` is not a `ValueError`, and the call sits after the `try`. Both facts are load-bearing; Step 1 pins them.

- [ ] **Step 1: Write the failing tests**

```python
from clarinet.files._template import RenderMode, render_template


class TestRenderTemplatePathSafe:
    def test_off_by_default(self):
        out = render_template("{patient_id}", {"patient_id": "/etc/passwd"})
        assert out == "/etc/passwd"

    def test_rejects_when_enabled(self):
        with pytest.raises(UnsafePathError):
            render_template("{patient_id}", {"patient_id": "/etc/passwd"}, path_safe=True)

    def test_lenient_mode_does_not_swallow_the_violation(self):
        # _replace swallows ValueError in LENIENT mode and substitutes "".
        # UnsafePathError must escape that handler, or every violation would be
        # silently rewritten to an empty string.
        with pytest.raises(UnsafePathError):
            render_template(
                "seg_{patient_id}.nrrd",
                {"patient_id": "../../etc/passwd"},
                mode=RenderMode.LENIENT,
                path_safe=True,
            )

    def test_runs_after_coercion(self):
        # A list coerces to "a_b" by default; with "/" as the separator it
        # becomes "a/b", which only a post-coercion check can catch.
        with pytest.raises(UnsafePathError):
            render_template(
                "{mods}", {"mods": ["a", "b"]}, list_separator="/", path_safe=True
            )

    def test_missing_substitution_is_not_a_violation(self):
        assert render_template("f_{nope}.txt", {"x": 1}, path_safe=True) == "f_.txt"

    def test_legitimate_values_pass(self):
        out = render_template(
            "seg_{id}_{mods}.seg.nrrd", {"id": 7, "mods": ["CT", "SR"]}, path_safe=True
        )
        assert out == "seg_7_CT_SR.seg.nrrd"
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_path_safety.py -q -k RenderTemplatePathSafe`
Expected: FAIL — `TypeError: render_template() got an unexpected keyword argument 'path_safe'`

- [ ] **Step 3: Implement**

Add the parameter to the signature of `render_template` (after `on_missing_leave_as_is`):

```python
    path_safe: bool = False,
```

Extend the docstring's "Safety checks" paragraph to read:

```
    Safety checks (``/``, ``\\``, NUL, and a value that is exactly ``.`` or
    ``..``) are applied to each substituted value only when ``path_safe=True``.
    Containment and leading-dot checks on the *assembled* path remain in
    ``join_within``. Callers that render a filesystem path pass it; callers that
    render a free-form string (Slicer script arguments, which may legitimately
    be absolute paths) do not.
```

Then, inside `_replace`, change the final `return coerced` to:

```python
        # NOTE: outside the try/except above on purpose. In LENIENT mode that
        # handler swallows ValueError and substitutes `missing`; a guard raising
        # inside it would silently rewrite every violation to "". UnsafePathError
        # is not a ValueError, so it escapes regardless — both properties are
        # pinned by tests in tests/test_path_safety.py.
        if path_safe:
            assert_path_safe_value(key, coerced)
        return coerced
```

- [ ] **Step 4: Run and watch it pass**

Run: `uv run pytest tests/test_path_safety.py tests/test_path_template_renderer.py -q`
Expected: PASS. `test_path_template_renderer.py` must still collect **52** tests and pass unchanged — `path_safe` defaults to `False`, so nothing existing changes behaviour.

- [ ] **Step 5: Commit**

```bash
git add clarinet/files/_template.py tests/test_path_safety.py
git commit -m "feat(files): add opt-in path_safe rendering to render_template"
```

---

## Task 5: Wire the facade render→join sites

**Files:**
- Modify: `clarinet/files/facade.py:144-152` (`resolve`), `:168-169` (`render`), `:171-180` (`render_for`), `:203-206` (`checksums` singular branch)
- Modify: `clarinet/files/__init__.py`
- Test: `tests/test_files_facade.py` (16 tests today)

**Interfaces:**
- Consumes: `assert_path_safe_value`, `join_within`, `render_template(..., path_safe=True)`.
- Produces: `Files.resolve`, `Files.exists`, `Files.checksums`, `Files.render`, `Files.render_for` all raising `UnsafePathError` on a violation. `clarinet.files.validate_file_pattern` becomes importable (used by Task 7).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_files_facade.py`, following the fixtures already in that file:

```python
class TestPathSafety:
    def test_resolve_rejects_absolute_value(self, record_factory):
        record = record_factory(patient_id="/etc/passwd")
        f = Files(record)
        with pytest.raises(UnsafePathError):
            f.resolve("mask")

    def test_resolve_rejects_traversal_value(self, record_factory):
        record = record_factory(patient_id="../../etc")
        with pytest.raises(UnsafePathError):
            Files(record).resolve("mask")

    def test_exists_inherits_the_guard(self, record_factory):
        # exists() delegates to resolve(); this pins that it has no separate path.
        record = record_factory(patient_id="../../etc")
        with pytest.raises(UnsafePathError):
            Files(record).exists("mask")

    async def test_checksums_rejects_escaping_name(self, record_factory):
        # The singular branch performs its own working_dir / filename join and
        # is a live bypass if only resolve() is guarded.
        record = record_factory(patient_id="../../etc")
        with pytest.raises(UnsafePathError):
            await Files(record).checksums()

    def test_render_is_path_safe(self, record_factory):
        record = record_factory(patient_id="/etc/passwd")
        with pytest.raises(UnsafePathError):
            Files(record).render("{patient_id}.nrrd")

    def test_render_for_is_path_safe(self, record_factory):
        record = record_factory(patient_id="/etc/passwd")
        with pytest.raises(UnsafePathError):
            Files.render_for(record, "{patient_id}.nrrd")

    def test_static_render_template_stays_unguarded(self):
        # Slicer script args may legitimately be absolute paths.
        assert Files.render_template("{p}", {"p": "/opt/slicer/data"}) == "/opt/slicer/data"

    def test_subdirectory_pattern_still_resolves(self, record_factory):
        # NEW coverage: no test in the suite exercised a subdirectory pattern
        # before this change, despite the design relying on it staying legal.
        record = record_factory(study_uid="1.2.840")
        path = Files(record).render("{study_uid}/mask.nrrd")
        assert path == "1.2.840/mask.nrrd"

    def test_multi_dot_basename_still_renders(self, record_factory):
        record = record_factory()
        assert Files(record).render("mask.seg.nrrd") == "mask.seg.nrrd"
```

> Adapt `record_factory` to whatever fixture `tests/test_files_facade.py` already
> uses to build a `RecordRead` with a file registry — read the top of that file
> first and reuse it rather than introducing a second factory. If the existing
> tests build records inline, build them inline here too.

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/test_files_facade.py -q -k PathSafety`
Expected: FAIL — the guards do not exist, so `resolve` returns an escaping path instead of raising.

- [ ] **Step 3: Implement**

`clarinet/files/facade.py`, `resolve`:

```python
    def resolve(self, file_def: FileDefArg, **overrides: Any) -> Path:
        fd = self._lookup(file_def)
        working_dir = self._dirs[fd.level or self._level]
        filename = _template.render_template(
            fd.pattern,
            {**self._fields, **overrides},
            mode=_template.RenderMode.LENIENT,
            path_safe=True,
        )
        path = _template.join_within(working_dir, filename)
        self._accessed.setdefault(fd.name, path)
        return path
```

`checksums`, singular branch:

```python
            else:
                filename = _template.render_template(
                    fd.pattern,
                    self._fields,
                    mode=_template.RenderMode.LENIENT,
                    path_safe=True,
                )
                c = await _checksums.compute_file_checksum(
                    _template.join_within(working_dir, filename)
                )
```

`render` and `render_for` — add `path_safe=True` to both calls. They return a `str` and have no base to join against, so no `join_within`.

Leave `Files.render_template` (line 182) exactly as it is. Add above it:

```python
    # Deliberately NOT path-safe: this renders Slicer script arguments
    # (services/slicer/context.py), which may legitimately be absolute paths.
    # It never feeds a working-directory join. See design.md D3.
```

`clarinet/files/__init__.py` — extend `__all__` and `__getattr__`:

```python
__all__ = [
    "PLACEHOLDER_REGEX",
    "AnonPathError",
    "Files",
    "assert_path_safe_value",
    "join_within",
    "validate_file_pattern",
]
```

```python
    if name in ("assert_path_safe_value", "join_within", "validate_file_pattern"):
        from clarinet.files import _template

        return getattr(_template, name)
```

and add the matching `if TYPE_CHECKING:` imports at the top.

> `validate_file_pattern` does not exist until Task 6. Implement `__getattr__`
> now but do **not** add `validate_file_pattern` to `__all__` until Task 6, or
> `from clarinet.files import *` breaks in between. Simpler: do the whole
> `__init__.py` edit in Task 6 and only wire the facade here.

- [ ] **Step 4: Run and watch them pass**

Run: `uv run pytest tests/test_files_facade.py -q`
Expected: PASS, 16 baseline + 9 new = **25** tests.

- [ ] **Step 5: Commit**

```bash
git add clarinet/files/facade.py clarinet/files/__init__.py tests/test_files_facade.py
git commit -m "feat(files): guard every render-then-join site in the Files facade"
```

---

## Task 6: `validate_file_pattern` and the `{data.*}` ban

**Files:**
- Modify: `clarinet/files/_template.py`
- Modify: `clarinet/files/__init__.py`
- Test: `tests/test_path_safety.py`

**Interfaces:**
- Consumes: `extract_placeholders` (already in `_template.py:81`), `_PLACEHOLDER_RE`.
- Produces: `validate_file_pattern(pattern: str) -> str`, raising `ValueError`. Task 7 attaches it as a Pydantic validator.

**Why `ValueError` and not `UnsafePathError`:** it matches `validate_name_is_identifier` on the same models, so Pydantic turns it into a 422 on the API path and the config loader turns it into a `ConfigLoadError`. Raising a `ClarinetError` from a Pydantic validator would produce a 500 instead.

- [ ] **Step 1: Write the failing tests**

```python
from clarinet.files._template import validate_file_pattern

# Un-banning under #552 moves entries from this list to LEGAL_PATTERNS below.
BANNED_DATA_PATTERNS = [
    "birads_{data.BIRADS_R}.txt",
    "report_{data.timepoint}.pdf",
    "{data.side}_mask.nrrd",
    "{data}",
    "seg_{id}_{data.lesion}.nrrd",
]

UNSAFE_LITERAL_PATTERNS = [
    "/abs/mask.nrrd",
    "../x.nrrd",
    "sub/../x.nrrd",
    "back\\slash.nrrd",
    "outputs/",
    "",
    "   ",
    ".hidden.nrrd",
    "nul\x00.nrrd",
]

LEGAL_PATTERNS = [
    "mask.nrrd",
    "mask.seg.nrrd",
    "seg_{id}.seg.nrrd",
    "segmentation_{user_id}.seg.nrrd",
    "{study_uid}/mask.nrrd",
    "master_model.seg.nrrd",
    "report_{parent_id}.pdf",
]


class TestValidateFilePattern:
    @pytest.mark.parametrize("pattern", BANNED_DATA_PATTERNS)
    def test_rejects_data_placeholders(self, pattern):
        with pytest.raises(ValueError, match="data"):
            validate_file_pattern(pattern)

    def test_ban_error_suggests_replacements(self):
        with pytest.raises(ValueError) as exc:
            validate_file_pattern("birads_{data.BIRADS_R}.txt")
        message = str(exc.value)
        assert "data.BIRADS_R" in message
        assert "{id}" in message

    @pytest.mark.parametrize("pattern", UNSAFE_LITERAL_PATTERNS)
    def test_rejects_unsafe_literal_text(self, pattern):
        with pytest.raises(ValueError):
            validate_file_pattern(pattern)

    @pytest.mark.parametrize("pattern", LEGAL_PATTERNS)
    def test_accepts(self, pattern):
        assert validate_file_pattern(pattern) == pattern

    def test_placeholder_content_is_masked_before_literal_check(self):
        # A placeholder NAME containing a dot must not trip the dot-leading
        # basename rule, and a placeholder standing alone as the basename is fine.
        assert validate_file_pattern("{record_type.name}.nrrd") == "{record_type.name}.nrrd"
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_path_safety.py -q -k ValidateFilePattern`
Expected: FAIL — `ImportError: cannot import name 'validate_file_pattern'`

- [ ] **Step 3: Implement**

Append to `clarinet/files/_template.py`:

```python
def _reject_data_placeholders(pattern: str) -> None:
    """Reject ``{data}`` / ``{data.FIELD}`` placeholders in a file pattern.

    TEMPORARY — tracked by issue #552, which will reintroduce the placeholder
    once the non-traversal corner cases are settled (length limits,
    Windows-reserved basenames, unicode normalization, PHI in filenames, and
    the fact that ``record.data`` is mutable, so a value-derived path changes
    on every submit and orphans the file already written).

    Un-banning is: delete this function and its one call in
    ``validate_file_pattern``. The literal-text rules in that function are
    permanent and stay.
    """
    offenders = sorted(
        name
        for name in extract_placeholders(pattern)
        if name == "data" or name.startswith("data.")
    )
    if offenders:
        raise ValueError(
            f"file pattern may not interpolate record data: {', '.join(offenders)}. "
            f"Use {{id}}, {{parent_id}} or {{user_id}} instead, or declare one "
            f"FileDefinition per variant. This restriction is temporary — see "
            f"https://github.com/radionest/clarinet/issues/552"
        )


def validate_file_pattern(pattern: str) -> str:
    """Validate a ``FileDefinition.pattern`` at configuration-load time.

    Raises ``ValueError`` (not ``UnsafePathError``) to match
    ``validate_name_is_identifier`` on the same models: Pydantic turns it into a
    422 on the API path, and the config loader into a ``ConfigLoadError``.
    """
    _reject_data_placeholders(pattern)

    # Mask placeholders out — only admin-authored literal text is checked here.
    literal = _PLACEHOLDER_RE.sub("\x01", pattern)
    if not literal.strip() and not pattern.strip():
        raise ValueError("file pattern must not be empty")
    if pattern.startswith(("/", "\\")):
        raise ValueError(f"file pattern must be relative, got {pattern!r}")
    if "\\" in literal:
        raise ValueError(f"file pattern must not contain a backslash, got {pattern!r}")
    if "\x00" in literal:
        raise ValueError("file pattern must not contain a NUL byte")
    if pattern.endswith(("/", "\\")):
        raise ValueError(f"file pattern must not end in a separator, got {pattern!r}")
    if ".." in PurePosixPath(literal).parts:
        raise ValueError(f"file pattern must not contain a '..' component, got {pattern!r}")
    basename = PurePosixPath(literal).name
    if basename.startswith("."):
        raise ValueError(f"file pattern basename must not start with a dot, got {pattern!r}")
    return pattern
```

> `\x01` is the masking sentinel: it is not a path separator, not a dot, and
> cannot appear in a pattern that passed the NUL check, so a placeholder never
> satisfies or trips a literal rule by accident. A pattern that is *only* a
> placeholder therefore has basename `\x01`, which is legal — the value guard
> and `join_within` cover what it renders to.

Now complete `clarinet/files/__init__.py` with the `__all__` and `__getattr__` edits sketched in Task 5.

- [ ] **Step 4: Run and watch it pass**

Run: `uv run pytest tests/test_path_safety.py -q`
Expected: PASS. Also run `uv run python -c "from clarinet.files import validate_file_pattern; print(validate_file_pattern('mask.nrrd'))"` — expected output `mask.nrrd`, proving the lazy re-export works without importing the private leaf.

- [ ] **Step 5: Commit**

```bash
git add clarinet/files/_template.py clarinet/files/__init__.py tests/test_path_safety.py
git commit -m "feat(files): validate file patterns at config load, temporarily banning {data.*}"
```

---

## Task 7: Attach the validator to `FileDefinitionRead`

**Files:**
- Modify: `clarinet/models/file_schema.py:143-178` (`FileDefinitionRead`)
- Test: `tests/test_config_primitives.py` (6), `tests/test_file_registry_resolver.py` (20), `tests/test_file_validation.py` (18), `tests/test_path_uniqueness.py` (10)

**Interfaces:**
- Consumes: `clarinet.files.validate_file_pattern`.
- Produces: `FileDefinitionRead(pattern=…)` raising `ValidationError` for a banned or unsafe pattern.

**Do not add a validator to `FileDefinition`.** It is `table=True`, and SQLModel skips Pydantic validation on table models — its existing `validate_name_is_identifier` never fires either. Verified:

```
FileDefinition:     NO validation on __init__
FileDefinitionRead: validated -> ValidationError
```

A second validator there would read as an enforcement point that does not exist. The upside: existing `filedefinition` rows are never re-validated on load, so no deployed database is invalidated.

- [ ] **Step 1: Write the failing tests**

In `tests/test_config_primitives.py`:

```python
def test_file_def_rejects_data_placeholder():
    with pytest.raises(ValidationError, match="data.timepoint"):
        FileDef(name="rep", pattern="report_{data.timepoint}.pdf", level="SERIES")


def test_file_def_rejects_absolute_pattern():
    with pytest.raises(ValidationError):
        FileDef(name="rep", pattern="/abs/report.pdf", level="SERIES")


def test_file_def_accepts_subdirectory_pattern():
    assert FileDef(name="rep", pattern="{study_uid}/report.pdf", level="SERIES")
```

In `tests/test_file_registry_resolver.py`, the same two rejections via `FileRegistryEntry`, matching whatever construction helper that file already uses.

In `tests/test_path_safety.py`, the model-level pair:

```python
def test_file_definition_read_rejects_banned_pattern():
    from pydantic import ValidationError

    from clarinet.models.file_schema import FileDefinitionRead

    with pytest.raises(ValidationError):
        FileDefinitionRead(name="birads_file", pattern="birads_{data.BIRADS_R}.txt")


def test_table_model_is_intentionally_unvalidated():
    # Pins the reason there is no validator on FileDefinition: SQLModel skips
    # validation on table=True models, so one there would be dead code. If this
    # ever starts raising, add the validator and delete this test.
    from clarinet.models.file_schema import FileDefinition

    assert FileDefinition(name="1bad", pattern="birads_{data.X}.txt").name == "1bad"
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/test_config_primitives.py tests/test_file_registry_resolver.py tests/test_path_safety.py -q`
Expected: FAIL on the new rejection tests; `test_table_model_is_intentionally_unvalidated` already passes.

- [ ] **Step 3: Implement**

In `clarinet/models/file_schema.py`, add to `FileDefinitionRead` beside `validate_name_is_identifier`:

```python
    @field_validator("pattern")
    @classmethod
    def validate_pattern_is_path_safe(cls, v: str) -> str:
        """Reject patterns that could resolve outside the working directory.

        Attached here and NOT to ``FileDefinition``: that model is
        ``table=True``, so SQLModel skips Pydantic validation on it and a
        validator there would never fire. Every real entry point —
        ``config/primitives.py`` (``FileDef``),
        ``utils/file_registry_resolver.py`` (``FileRegistryEntry``) and the API
        POST/PATCH paths — constructs ``FileDefinitionRead``.
        """
        from clarinet.files import validate_file_pattern

        return validate_file_pattern(v)
```

> The import is function-local, following the existing precedent of
> `validate_output_path_uniqueness` being lazily imported inside
> `models/record_type.py` to dodge the config↔models cycle.

Update the class docstring for `FileDefinition.pattern` (line 42-44) to mark `{data.FIELD}` temporarily rejected with a link to #552.

- [ ] **Step 4: Run and watch them pass**

Run: `uv run pytest tests/test_config_primitives.py tests/test_file_registry_resolver.py tests/test_file_validation.py tests/test_path_uniqueness.py tests/test_path_safety.py -q`
Expected: the two collateral files now FAIL — `tests/test_file_validation.py` builds a `FileDefinitionRead` with `birads_{data.BIRADS_R}.txt`, and `tests/test_path_uniqueness.py` builds record types with `report_{data.timepoint}.pdf`. Fix them in Step 5, not by weakening the validator.

- [ ] **Step 5: Fix the two collateral test files**

`tests/test_file_validation.py` — change the pattern to `birads_{id}.txt` and add at the changed line:

```python
                pattern="birads_{id}.txt",  # was {data.BIRADS_R}; see #552
```

`tests/test_path_uniqueness.py` (two sites, lines 86 and 98) — change `report_{data.timepoint}.pdf` to `report_{origin_type}.pdf`, with the same comment. `{origin_type}` keeps both tests meaningful: it is a placeholder that deliberately does **not** satisfy the uniqueness rule, which is exactly what `test_per_file_optout_suppresses` and `test_optout_does_not_cover_siblings` are asserting.

- [ ] **Step 6: Re-run and commit**

Run: `uv run pytest tests/test_config_primitives.py tests/test_file_registry_resolver.py tests/test_file_validation.py tests/test_path_uniqueness.py tests/test_path_safety.py -q`
Expected: PASS. Counts: `test_config_primitives.py` 6→9, `test_file_registry_resolver.py` 20→22, `test_file_validation.py` 18, `test_path_uniqueness.py` 10.

```bash
git add clarinet/models/file_schema.py tests/
git commit -m "feat(models): validate FileDefinitionRead.pattern at config load"
```

---

## Task 8: Persisted-filename replay

**Files:**
- Modify: `clarinet/services/pipeline/context.py:146` (`return f.dir(level) / link.filename`)
- Test: `tests/test_pipeline_context.py` (69 tests today)

**Interfaces:**
- Consumes: `join_within`.
- Produces: no new symbol; the existing resolution helper now raises `UnsafePathError`.

Nothing is rendered here — the filename comes from a `RecordFileLink` row — so only the containment check applies, no value guard.

- [ ] **Step 1: Write the failing test**

```python
def test_persisted_filename_traversal_is_refused(self, ...):
    # A RecordFileLink row written before the guard existed must be refused at
    # read time rather than followed. No migration: the guard IS the remediation.
    record = ...  # build a record whose file_links carry the hostile filename
    record.file_links = [RecordFileLinkRead(name="mask", filename="../../etc/passwd")]
    with pytest.raises(UnsafePathError):
        _resolve_file_path(record, "mask", type_name="test_task")
```

> Read the surrounding class in `tests/test_pipeline_context.py` first and reuse
> its record-building helper and the real name of the resolution function; the
> file already has 69 tests covering this module, including
> `_resolve_pattern_from_dict`.

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_pipeline_context.py -q -k persisted_filename`
Expected: FAIL — the join returns `/etc/passwd` instead of raising.

- [ ] **Step 3: Implement**

```python
                    return _template.join_within(f.dir(level), link.filename)
```

Import `_template` at the top of `clarinet/services/pipeline/context.py` if it is not already imported; prefer `from clarinet.files import join_within` so the module does not reach into the private leaf.

- [ ] **Step 4: Run and watch it pass**

Run: `uv run pytest tests/test_pipeline_context.py -q`
Expected: PASS, 69 baseline + 1 = **70** tests.

- [ ] **Step 5: Commit**

```bash
git add clarinet/services/pipeline/context.py tests/test_pipeline_context.py
git commit -m "fix(pipeline): confine persisted RecordFileLink filenames to the working dir"
```

---

## Task 9: 422 on record submit, and the PHI-safe warning

**Files:**
- Modify: `clarinet/services/record_service.py:97` (`_missing_output_links`)
- Test: `tests/test_record_service.py` (or the existing submit-path test module)

**Interfaces:**
- Consumes: `UnsafePathError`, `clarinet.exceptions.http.UNPROCESSABLE_ENTITY`.
- Produces: a 422 on the submit path; every other site keeps propagating.

- [ ] **Step 1: Write the failing tests**

```python
async def test_submit_with_poisoned_data_returns_422(...):
    # A user's own bad data is a 422, not a 500.
    ...
    with pytest.raises(HTTPException) as exc:
        await record_service.check_files(record)
    assert exc.value.status_code == 422


def test_violation_warning_omits_the_value(caplog, ...):
    # record.data may carry PHI — the value belongs in the exception message,
    # which reaches only the submitter, never in a log.
    ...
    assert "SECRET_MRN_VALUE" not in caplog.text
    assert "patient_id" in caplog.text
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/test_record_service.py -q -k "422 or warning_omits"`
Expected: FAIL — `UnsafePathError` propagates as a 500-equivalent.

- [ ] **Step 3: Implement**

In `_missing_output_links`, wrap the `Files.render_for` call:

```python
        try:
            rendered = Files.render_for(record, fd.pattern, parent=parent)
        except UnsafePathError as exc:
            logger.warning(
                f"unsafe output path rejected for record {record.id}, "
                f"file definition '{fd.name}': {exc}"
            )
            raise UNPROCESSABLE_ENTITY.with_context(
                f"File '{fd.name}' cannot be resolved from the submitted data: "
                f"{exc} (offending value: {exc.value!r})"
            ) from exc
        missing[name] = collection_file or rendered
```

> Per the amendment in Global Constraints, `str(exc)` no longer embeds the
> rejected value — it names the placeholder key and the reason only, so it is
> safe to log directly and is strictly more useful than the bare class name.
> The value lives on `exc.value` and reaches only the submitter, through the
> 422 body. Do not add `exc.value` to the log line.

Emit the analogous warning inside `assert_path_safe_value`? **No** — that
primitive is in the stdlib-only leaf and must not import `clarinet.utils.logger`.
Logging stays at the service boundary, where the record id is in scope.

- [ ] **Step 4: Run and watch them pass**

Run: `uv run pytest tests/test_record_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add clarinet/services/record_service.py tests/test_record_service.py
git commit -m "feat(records): return 422 when submitted data yields an unsafe output path"
```

---

## Task 10: Documentation and CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`, `clarinet/models/file_schema.py` (docstring, if not already done in Task 7), `.claude/rules/file-registry.md:21`, `clarinet/docs/file-registry.md:21`, `clarinet/docs/agent/claude/definitions.md:41`, `clarinet/docs/agent/claude/workflows.md:129`, `examples/project_template/.claude/rules/definitions.md:42` and `:66`, `examples/project_template/.claude/rules/workflows.md:129`, `docs/kb/files-and-anonymization.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: CHANGELOG**

Add under the unreleased heading:

```markdown
### Breaking

- **File patterns may no longer interpolate record data.** A
  `FileDefinition.pattern` containing `{data.FIELD}` (or bare `{data}`) is now
  rejected when the configuration loads. Migration: replace it with `{id}`,
  `{parent_id}` or `{user_id}`, or declare one `FileDefinition` per variant. If
  files already exist on disk under the old name, rename them to match the new
  pattern — the resolved filename changes with the pattern. This restriction is
  temporary and tracked by #552.

### Security

- Rendered file paths are now confined to the record's working directory. A
  substituted value containing `/`, `\`, NUL or a bare `..` is rejected, and the
  joined path is checked for containment. Closes #521.
- A `RecordFileLink.filename` row persisted with a traversal before this release
  is now refused at read time instead of being followed. No migration is needed —
  the guard is the remediation.
```

- [ ] **Step 2: The eight `{data.FIELD}` mention sites**

Wording at each: *`{data.FIELD}` — temporarily rejected, see [#552](https://github.com/radionest/clarinet/issues/552)*. Keep the placeholder visible in the grammar; do **not** delete the row. A downstream author who reads "removed" re-architects their file registry and renames files on disk; one who reads "temporarily rejected" substitutes `{id}` and waits.

`.claude/rules/file-registry.md:21` and `clarinet/docs/file-registry.md:21` are **byte-identical**; make the same edit in both and verify with `diff -q`.

`examples/project_template/.claude/rules/definitions.md:66` is the exception: it is the `allow_path_collision` row, which cites `{data.FIELD}` as its motivating example. Replace the example rather than annotating it — `allow_path_collision` is unaffected by the ban, and `config/path_uniqueness.py` never counted `{data.*}` as a discriminator anyway. Use: *the author guarantees uniqueness some other way, e.g. one FileDefinition per variant*.

- [ ] **Step 3: Knowledge-base page**

`docs/kb/files-and-anonymization.md` has no existing `{data.FIELD}` mention. Add a section covering both layers: the value guard and `join_within` at render→join sites, `_filter_in_sandbox` remaining the symlink-aware layer at the delete and serve sinks, the config-time pattern validator, and where `UnsafePathError` surfaces (422 on submit, 500 elsewhere, retry/DLQ in the pipeline).

- [ ] **Step 4: Verify the mirrors and the cross-references**

```bash
diff -q .claude/rules/file-registry.md clarinet/docs/file-registry.md
grep -rn '552' CLAUDE.md .claude/rules/ clarinet/docs/ examples/ docs/kb/ clarinet/models/file_schema.py clarinet/files/_template.py
```

Expected: `diff` silent; the grep hits every site from Step 2 plus `_reject_data_placeholders`.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md docs/ .claude/rules/ clarinet/docs/ examples/ clarinet/models/file_schema.py
git commit -m "docs: record the path-safety guards and the temporary {data.*} ban"
```

---

## Task 11: Full verification

**Files:** none modified unless a gate fails.

- [ ] **Step 1: Behavioural gate before the static one**

```bash
timeout 300 uv run pytest -q -k "path_safety or files_facade or file_registry_resolver or path_uniqueness or file_validation or path_template_renderer or pipeline_context" > /tmp/test-gfpt.txt 2>&1
```

Then read `/tmp/test-gfpt.txt`. Expected: **more than 186** collected (186 baseline + `test_path_safety.py`), zero failures. Exactly 186 means the new file was not collected — investigate rather than declaring green.

A type or lint pass does not catch a behavioural regression in a test that mocked the old contract, which is why this runs first.

- [ ] **Step 2: Full fast suite**

```bash
timeout 600 make test-fast > /tmp/test-gfpt-fast.txt 2>&1
```

Read the file. Any failure outside the files this plan touches must be reproduced at the branch base (`git merge-base main HEAD`) before being called pre-existing — record the SHA if so.

- [ ] **Step 3: Static checks**

```bash
make check
```

Expected: clean. After `make check`, re-`Read` any file before further edits — `ruff format` may have rewritten it.

- [ ] **Step 4: Resolve the open THEORY**

`design.md` carries: *THEORY: no deployed database holds a `{data.*}` `FileDefinition` row.* The scan behind it covered checked-out configurations only; a row created through `POST /file-definitions` would not appear in it.

```sql
SELECT name, pattern FROM filedefinition WHERE pattern LIKE '%{data.%';
```

Run this against each deployed instance. Record the result — confirmed or refuted, with the instance and date — in `verify.md`. A hit means that project needs the CHANGELOG migration applied by hand and its on-disk files renamed. **This must be resolved before release, not before merge.**

- [ ] **Step 5: Pre-PR review**

Run `Agent(subagent_type=pr-diff-reviewer)` before the first `gh pr create` — the hook gates on it, and `SKIP_PR_REVIEW=1` is forbidden without an explicit request from the user.

- [ ] **Step 6: Open the PR**

```bash
git push -u origin <branch>
gh pr create --title "fix(files): confine rendered file paths to the record working directory" --body "..."
```

The body should state that it closes #521, that #552 tracks reintroducing `{data.*}`, and that the ban is a breaking change with a migration note in the CHANGELOG.

---

---

## Task 12: Close the two remaining containment gaps

> **Added mid-execution.** Both gaps were found during Task 5 and neither is in the
> original File Structure table. Execute this task **after Task 9 and before Task 10**,
> so the documentation task covers it.

**Files:**
- Modify: `clarinet/services/file_validation.py` (the `(target_dir / resolved)` join)
- Modify: `clarinet/files/_resolver.py` (the `base = record.clarinet_storage_path or ...` line)
- Modify: `clarinet/models/record.py` (`RecordCreate` fail-fast validator)
- Test: `tests/test_file_validation.py`, `tests/test_files_facade.py`, and the record-create test module

**Interfaces:**
- Consumes: `join_within`, `UnsafePathError`, `settings.storage_path`.
- Produces: no new public symbol.

### Part A — `file_validation.py` is an unguarded render→join site

`clarinet/services/file_validation.py:110` reads:

```python
filename = resolved if (target_dir / resolved).is_file() else None
```

`resolved` comes from `Files.render_for(...)`, which Task 5 made `path_safe=True`, so the
*value* half is covered. The *join* is not. `design.md`'s sink table lists this file
("existence probe; the string is then persisted as `RecordFileLink.filename`"), but the
plan's File Structure table omits it.

The residual vector is an escaping **pattern literal** rather than a value — e.g. a
`filedefinition` row carrying `../escape.nrrd`. Per D7 such rows are never re-validated on
load, so Task 6/7's `validate_file_pattern` never sees them. The probe then stats outside
the working directory and, on a hit, persists the escaping name as
`RecordFileLink.filename` — which Task 8 refuses on replay, but only after it is stored.

- [ ] **Step 1:** Write a failing test in `tests/test_file_validation.py`: a file
      definition whose pattern is a literal `../escape.nrrd` (no placeholder, so the value
      guard cannot fire) must raise `UnsafePathError` rather than probing outside
      *target_dir*. Read the file's existing fixtures first and reuse them.
- [ ] **Step 2:** Run it; watch it fail (the probe returns cleanly today).
- [ ] **Step 3:** Replace the bare join with `join_within(target_dir, resolved)`, importing
      it from `clarinet.files` (the public lazy re-export added in Task 6), never from the
      private `_template` leaf.
- [ ] **Step 4:** Re-run. `tests/test_file_validation.py` was **18** before this change.
- [ ] **Step 5:** Commit — `fix(files): confine the file-validation existence probe to the working dir`

### Part B — the containment base is caller-chosen

`clarinet/files/_resolver.py:87` computes `base = record.clarinet_storage_path or
settings.storage_path`. `clarinet_storage_path` is declared on `RecordBase`
(`clarinet/models/record.py:169`), so `RecordCreate` inherits it
(`clarinet/models/record.py:284`), `POST /api/records` builds the row with
`Record(**new_record.model_dump())` (`clarinet/api/routers/record.py:368`), and
`RecordService.create_record` (`clarinet/services/record_service.py:190`) never resets it.
Any authenticated user therefore chooses the directory that `join_within` measures
containment against — which makes the guard's own base untrusted.

**This is a real per-record override feature, not dead code** — 40 references across 8 test
modules, including `tests/integration/test_record_working_folder.py`. Do not delete the
field or make it server-only without first reading that integration test and the other
call sites; breaking a legitimate workflow is not an acceptable way to close this.

> **SUPERSEDED DESIGN — read this before the steps.** The original steps here
> prescribed rejecting a `clarinet_storage_path` not contained under
> `settings.storage_path`. The Part B investigation disproved that premise:
> the field is *intentionally* a disjoint absolute root, pinned by
> `tests/integration/test_record_working_folder.py` (which asserts
> `not str(rendered).startswith(str(settings.storage_path))`),
> `tests/test_file_repository.py` and `tests/test_pipeline_context.py`, and
> recorded as intentional in commit `2eb2448`. Human ruling: normalize at the
> resolver + gate who may set it. No containment clause.

- [x] **Step 1: Investigate before implementing.** DONE. Finding: a legitimate,
      tested use DOES escape `settings.storage_path`, by design. 41 references
      across 9 test modules. Only server-side writer is
      `db_scrub/scrubber.py:279`, which nulls the field; no production code sets
      a non-`None` value.

- [ ] **Step 2 — Half 1, the resolver.** In `clarinet/files/_resolver.py` (the
      `base = record.clarinet_storage_path or settings.storage_path` line), reject a
      set `clarinet_storage_path` that is **not absolute** or **not normalized**.
      No containment check. Enforce here rather than only at the API so it covers
      every consumer (`Files`, the Slicer context builder at
      `services/slicer/context.py:282`) and every row however it was created,
      including rows already in a deployed database.

      Why this half exists, and it belongs in a code comment: `join_within` is
      purely lexical (D4), so if `base` itself is relative or carries an embedded
      `..`, its containment proof is **vacuous** — `base = "../../../../etc"`
      satisfies `is_relative_to(base)` for every filename joined onto it. This is
      what makes every other guard in the change mean something.

      Raise `UnsafePathError` with the offending path in `value=`, not in the
      message, keeping the PHI contract uniform.

- [ ] **Step 3 — Half 2, the API boundary.** Restrict *who* may set the field, not
      *where* it points: a non-admin supplying a non-`None` `clarinet_storage_path`
      is rejected. A Pydantic `field_validator` cannot see the current user, so this
      lives where the authenticated principal is in scope. Cover **both** create
      paths — `api/routers/record.py:368` and the bulk path at `:1234` — and check
      whether any update route can set the field (`RecordOptional` appears not to
      inherit `RecordBase`; verify rather than assume). Reuse the existing
      admin/superuser DI alias from `api/dependencies.py`.

- [ ] **Step 4:** Tests, TDD, one commit per half. Half 1: a relative base and a
      `..`-bearing base are both refused, including the vacuous-proof case
      `"../../../../etc"`. Half 2: a non-admin setting the field is rejected, an
      admin succeeds, and — guarding against the over-rejection failure that bit
      Task 9 twice — a non-admin creating a record **without** the field is
      unaffected.

- [ ] **Step 5:** The three disjoint-root tests must still pass **unchanged**:
      `tests/integration/test_record_working_folder.py`,
      `tests/test_file_repository.py`, `tests/test_pipeline_context.py`. Commit
      messages: `fix(files): reject a malformed per-record storage-path override`
      and `feat(api): restrict the per-record storage-path override to admins`.

**Accepted residual (human ruling):** a well-formed hostile absolute path set by an
admin, or already present in the database, is still honoured. Do not close it.

### Self-review for this task

The change is only complete if a caller can no longer choose a base outside the storage
root **and** the legitimate per-record override still works. State both explicitly in the
report, with the test that proves each.

## Self-Review

**Spec coverage** — every requirement in `specs/file-path-safety/spec.md` maps to a task:

| Requirement | Task |
|---|---|
| Pattern literal text is validated at configuration load | 6, 7 |
| `{data.*}` placeholders are rejected at configuration load | 6, 7 |
| Substituted values cannot contribute path separators | 2, 4, 5 |
| A rendered name is confined to the working directory | 3, 5 |
| Persisted filenames are confined on replay | 8 |
| A path violation never degrades into a fallback | 1 |
| Violations surface according to who caused them | 9 |
| Violation logs exclude the offending value | 9 |
| Non-path renderers stay unguarded | 5 (Step 3 comment + test) |
| Collection globbing is unaffected | covered by the unchanged `glob` path; `tests/test_files_facade.py` and `tests/test_file_patterns.py` (26) must stay green |

**Known gaps, stated rather than hidden:**

- Tasks 8 and 9 give test *shapes* rather than complete code, because both depend on record-building helpers already present in `tests/test_pipeline_context.py` and the record-service test module. Each step says to read the surrounding file and reuse its helper. Every other task carries complete, runnable code.
- The `record_factory` fixture in Task 5 is likewise named against whatever `tests/test_files_facade.py` already uses.
- No test in the suite exercised a subdirectory pattern before this change, and no documentation example uses one. Task 5's `test_subdirectory_pattern_still_resolves` is therefore new coverage. Whether such a pattern works end to end is still untested — nothing in `Files.resolve` creates the intermediate directory, and this plan does not change that.
