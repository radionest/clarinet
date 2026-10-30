# clarinet.scripting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `clarinet.scripting` — a typer-based frame (`@script` + `ScriptCtx`) standardizing options, client, tally, output, and exit codes for downstream operational scripts.

**Architecture:** One new module `clarinet/scripting.py` exposing `script()` (decorator factory that synthesizes a single-command typer app from the user's async function, appending four standard options) and `ScriptCtx` (write gate, limit, tally, confirm, lazy `ClarinetClient` from settings). No existing module changes. One new page joins the packaged agent-docs payload.

**Tech Stack:** Python 3.12, typer (new hard dep, base package), pytest + `typer.testing.CliRunner`, existing `clarinet.settings` / `clarinet.client`.

## Global Constraints

- Implementation runs in a git worktree (`EnterWorktree`); symlink `openspec` per the worktree rule before openspec commands (the apply skill does this).
- All Python via uv: `uv run pytest …`, `./scripts/run_tests.sh …`, `make check`. First command in a fresh worktree builds the venv — use `timeout 300` once.
- Test runs: redirect to a unique file (`> /tmp/test-scripting-tN.txt 2>&1`), never pipe. Don't re-run without an Edit between runs.
- Conventional commits, English, no Co-Authored-By tags.
- Operator-facing output via `typer.echo` (stdout); the frame itself does not log in normal flow. Never `print()`.
- `--limit` semantics belong to the script: the frame only supplies the value and the `hit_limit` predicate.
- `ClarinetClient` is imported lazily inside the `ctx.client` property — never at module top (spec: filesystem-only scripts must not construct or import it).
- typer pin `typer>=0.15`, no extras; apps created with `add_completion=False`.
- After `make check`, ruff may rewrite files — Read before any further Edit.
- Spec under test: `openspec/changes/add-scripting-frame/specs/script-frame/spec.md`; design decisions D1–D11 in `design.md`.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | modify (`dependencies`, ~line 55) | typer hard dep |
| `clarinet/scripting.py` | create | whole frame: `Tally`, `ScriptCtx`, `script()`, `ScriptEntry` |
| `tests/test_scripting.py` | create | unit + CliRunner tests, grows task by task |
| `clarinet/docs/agent/claude/scripting.md` | create | downstream author docs (agent payload) |
| `clarinet/docs/agent/claude/overview.md` | modify | one-line pointer to root `scripts/` |
| `tests/test_agent_scaffold.py` | modify (`SECTION_RULES`, line 26) | payload guard covers the new page |

---

## Task 1: typer dependency

**Files:**
- Modify: `pyproject.toml` (dependencies list, after `"pyyaml>=6.0",` ~line 55)

**Interfaces:**
- Produces: importable `typer` for every later task.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, after the line `    "pyyaml>=6.0",` insert:

```toml
    "typer>=0.15",
```

- [ ] **Step 2: Sync and verify**

Run: `timeout 300 uv sync > /tmp/test-scripting-t1.txt 2>&1`
Then: `uv run python -c "import typer; print(typer.__version__)"`
Expected: a version `>= 0.15`, no resolution errors in the log file. If uv cannot resolve `>=0.15` against existing pins, lower the floor to the highest resolvable and note it in the commit body.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): add typer as a hard dependency for clarinet.scripting"
```

---

## Task 2: Tally

**Files:**
- Create: `clarinet/scripting.py`
- Create: `tests/test_scripting.py`

**Interfaces:**
- Produces: `Tally` — `count(key, n=1)`, `__getitem__(key) -> int` (0 when absent), `fail(item, detail)`, `failed: list[tuple[str, str]]`, `summary_lines() -> list[str]`. Consumed by Tasks 3, 6, 7.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scripting.py`:

```python
"""Unit tests for clarinet.scripting — the @script frame for operational scripts."""

from __future__ import annotations

from clarinet.scripting import Tally


def test_tally_counts_and_getitem() -> None:
    tally = Tally()
    tally.count("checked")
    tally.count("checked")
    tally.count("created", 3)
    assert tally["checked"] == 2
    assert tally["created"] == 3
    assert tally["missing"] == 0


def test_tally_fail_records_and_summary() -> None:
    tally = Tally()
    tally.count("checked", 2)
    tally.fail("record 7", "boom")
    lines = tally.summary_lines()
    assert "checked: 2" in lines
    assert "failed: 1" in lines
    assert any("record 7" in line and "boom" in line for line in lines)


def test_tally_empty_summary() -> None:
    assert Tally().summary_lines() == ["failed: 0"]
```

- [ ] **Step 2: Run to verify failure**

Run: `./scripts/run_tests.sh tests/test_scripting.py -q > /tmp/test-scripting-t2a.txt 2>&1`
Expected: collection error — `ModuleNotFoundError: No module named 'clarinet.scripting'`.

- [ ] **Step 3: Implement Tally**

Create `clarinet/scripting.py`:

```python
"""Frame for downstream operational scripts (backfills, repairs, one-shots).

Standardizes the boilerplate every maintenance script repeats — standard CLI
options, dry-run gate, ClarinetClient construction, counters, summary, exit
codes — while leaving the algorithm (and what ``--limit`` counts) entirely to
the script. Scripts are dry-run by default; ``--commit`` enables writes.

Usage::

    from clarinet.scripting import script, ScriptCtx

    @script()
    async def main(ctx: ScriptCtx, series: str | None = None) -> None:
        \"\"\"Docstring becomes --help.\"\"\"
        async with ctx.client as client:
            ...

    if __name__ == "__main__":
        main()
"""

from __future__ import annotations

from collections import Counter


class Tally:
    """Named counters plus failure records for one script run.

    ``failed`` drives the process exit code (any failure -> exit 1), so record
    per-item errors here rather than raising when the run should continue.
    """

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.failed: list[tuple[str, str]] = []

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] += n

    def __getitem__(self, key: str) -> int:
        return self.counts[key]

    def fail(self, item: str, detail: str) -> None:
        self.failed.append((item, detail))

    def summary_lines(self) -> list[str]:
        lines = [f"{key}: {value}" for key, value in self.counts.items()]
        lines.append(f"failed: {len(self.failed)}")
        lines.extend(f"  {item}: {detail}" for item, detail in self.failed)
        return lines
```

- [ ] **Step 4: Run to verify pass**

Run: `./scripts/run_tests.sh tests/test_scripting.py -q > /tmp/test-scripting-t2b.txt 2>&1`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add clarinet/scripting.py tests/test_scripting.py
git commit -m "feat(scripting): Tally counters and failure records"
```

---

## Task 3: ScriptCtx core — write gate, limit, dry-run lines (tasks.md 2.1 + 2.2)

**Files:**
- Modify: `clarinet/scripting.py`
- Modify: `tests/test_scripting.py`

**Interfaces:**
- Consumes: `Tally` (Task 2).
- Produces: `ScriptCtx(*, commit=False, limit=None, yes=False, api_base=None)` with `.commit`, `.limit`, `.yes`, `.api_base`, `.tally`, `.hit_limit(count) -> bool`, `.would(msg) -> None`. Consumed by Tasks 4–7.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scripting.py` (extend the import line):

```python
from clarinet.scripting import ScriptCtx, Tally


def test_ctx_defaults() -> None:
    ctx = ScriptCtx()
    assert ctx.commit is False
    assert ctx.limit is None
    assert ctx.yes is False
    assert ctx.api_base is None
    assert isinstance(ctx.tally, Tally)


def test_hit_limit_unset() -> None:
    ctx = ScriptCtx()
    assert not ctx.hit_limit(0)
    assert not ctx.hit_limit(10_000)


def test_hit_limit_boundary() -> None:
    ctx = ScriptCtx(limit=2)
    assert not ctx.hit_limit(1)
    assert ctx.hit_limit(2)
    assert ctx.hit_limit(3)


def test_would_line(capsys) -> None:
    ScriptCtx().would("invalidate record 7")
    assert capsys.readouterr().out == "[dry-run] would invalidate record 7\n"
```

- [ ] **Step 2: Run to verify failure**

Run: `./scripts/run_tests.sh tests/test_scripting.py -q > /tmp/test-scripting-t3a.txt 2>&1`
Expected: ImportError on `ScriptCtx`.

- [ ] **Step 3: Implement ScriptCtx core**

In `clarinet/scripting.py`, add `import typer` after the `collections` import, and append:

```python
class ScriptCtx:
    """Runtime context handed to a framed script body as its first argument."""

    def __init__(
        self,
        *,
        commit: bool = False,
        limit: int | None = None,
        yes: bool = False,
        api_base: str | None = None,
    ) -> None:
        self.commit = commit
        self.limit = limit
        self.yes = yes
        self.api_base = api_base
        self.tally = Tally()

    def hit_limit(self, count: int) -> bool:
        """True iff ``--limit`` was given and ``count`` reached it.

        What ``count`` counts (checked / created / affected) is the script's
        decision — the frame never applies the limit itself.
        """
        return self.limit is not None and count >= self.limit

    def would(self, msg: str) -> None:
        """Uniform dry-run line: ``[dry-run] would <msg>``."""
        typer.echo(f"[dry-run] would {msg}")
```

- [ ] **Step 4: Run to verify pass**

Run: `./scripts/run_tests.sh tests/test_scripting.py -q > /tmp/test-scripting-t3b.txt 2>&1`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add clarinet/scripting.py tests/test_scripting.py
git commit -m "feat(scripting): ScriptCtx core - write gate, limit, dry-run lines"
```

---

## Task 4: ScriptCtx.confirm (tasks.md 2.3)

**Files:**
- Modify: `clarinet/scripting.py`
- Modify: `tests/test_scripting.py`

**Interfaces:**
- Produces: `ScriptCtx.confirm(msg) -> bool` — True without prompt under `--yes`; interactive on TTY; False (no blocking) on non-TTY without `--yes`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scripting.py` (add `import io`, `import sys`, `from unittest import mock`, `import pytest` to the imports):

```python
def test_confirm_yes_flag_skips_prompt(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("must not prompt"))
    assert ScriptCtx(yes=True).confirm("proceed?") is True


def test_confirm_non_tty_refuses(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # isatty() -> False
    assert ScriptCtx().confirm("proceed?") is False
    assert "--yes" in capsys.readouterr().out


def test_confirm_tty_prompts(monkeypatch) -> None:
    fake_stdin = mock.Mock()
    fake_stdin.isatty.return_value = True
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    assert ScriptCtx().confirm("proceed?") is True

    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    assert ScriptCtx().confirm("proceed?") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `./scripts/run_tests.sh tests/test_scripting.py -q > /tmp/test-scripting-t4a.txt 2>&1`
Expected: AttributeError — `ScriptCtx` has no `confirm`.

- [ ] **Step 3: Implement confirm**

Add `import sys` to `clarinet/scripting.py` imports; append to `ScriptCtx`:

```python
    def confirm(self, msg: str) -> bool:
        """Operator gate for destructive steps; ``--yes`` pre-approves.

        Refuses (returns False) instead of blocking when stdin is not a TTY —
        an unattended run must never hang on ``input()``.
        """
        if self.yes:
            return True
        if not sys.stdin.isatty():
            typer.echo(f"{msg} — refusing without --yes (stdin is not a TTY)")
            return False
        answer = input(f"{msg} Type 'yes' to proceed: ")
        return answer.strip().lower() == "yes"
```

- [ ] **Step 4: Run to verify pass**

Run: `./scripts/run_tests.sh tests/test_scripting.py -q > /tmp/test-scripting-t4b.txt 2>&1`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add clarinet/scripting.py tests/test_scripting.py
git commit -m "feat(scripting): ScriptCtx.confirm honoring --yes with non-TTY refusal"
```

---

## Task 5: Lazy settings-based client (tasks.md 2.4)

**Files:**
- Modify: `clarinet/scripting.py`
- Modify: `tests/test_scripting.py`

**Interfaces:**
- Consumes: `ClarinetClient(base_url, ..., verify_ssl=..., service_token=...)` (`clarinet/client.py:108`); `settings.effective_api_base_url` / `effective_service_token` (properties over `api_base_url` / `internal_service_token` / `admin_password`), `settings.api_verify_ssl` (plain field).
- Produces: `ScriptCtx.client` lazy cached property. Consumed by scripts (`async with ctx.client as client:`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scripting.py` (add `import clarinet.client` and `from clarinet.settings import settings` to imports):

```python
class _FakeClient:
    def __init__(self, base_url: str, **kwargs: object) -> None:
        self.base_url = base_url
        self.kwargs = kwargs


def test_client_lazy_cached_and_settings_based(monkeypatch) -> None:
    monkeypatch.setattr(clarinet.client, "ClarinetClient", _FakeClient)
    monkeypatch.setattr(settings, "api_base_url", "http://from-settings/api")
    ctx = ScriptCtx()
    first = ctx.client
    assert isinstance(first, _FakeClient)
    assert first.base_url == "http://from-settings/api"
    assert ctx.client is first  # cached


def test_client_api_base_override(monkeypatch) -> None:
    monkeypatch.setattr(clarinet.client, "ClarinetClient", _FakeClient)
    ctx = ScriptCtx(api_base="http://override:9999")
    assert ctx.client.base_url == "http://override:9999"


def test_client_passes_token_and_ssl(monkeypatch) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(clarinet.client, "ClarinetClient", _FakeClient)
    monkeypatch.setattr(settings, "api_base_url", "http://x/api")
    monkeypatch.setattr(settings, "api_verify_ssl", False)
    monkeypatch.setattr(settings, "internal_service_token", SecretStr("tok-123"))
    ctx = ScriptCtx()
    assert ctx.client.kwargs == {"service_token": "tok-123", "verify_ssl": False}


def test_no_client_when_untouched(monkeypatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("ClarinetClient must not be constructed")

    monkeypatch.setattr(clarinet.client, "ClarinetClient", _boom)
    ctx = ScriptCtx()
    ctx.tally.count("checked")
    assert ctx.hit_limit(5) is False
    # reaching here without AssertionError = no construction happened
```

- [ ] **Step 2: Run to verify failure**

Run: `./scripts/run_tests.sh tests/test_scripting.py -q > /tmp/test-scripting-t5a.txt 2>&1`
Expected: AttributeError — `ScriptCtx` has no `client`.

- [ ] **Step 3: Implement the property**

In `clarinet/scripting.py`, add a `TYPE_CHECKING` import block after the stdlib imports:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clarinet.client import ClarinetClient
```

In `ScriptCtx.__init__`, add:

```python
        self._client: ClarinetClient | None = None
```

Append to `ScriptCtx`:

```python
    @property
    def client(self) -> ClarinetClient:
        """Lazy ``ClarinetClient`` from settings; enter it with ``async with``.

        Lazy on purpose: filesystem/Slicer-only scripts never construct one
        (and never import ``clarinet.client``). ``--api-base`` overrides the
        settings-derived base URL; the service token comes from settings/env
        only — never from a CLI flag.
        """
        if self._client is None:
            from clarinet.client import ClarinetClient
            from clarinet.settings import settings

            self._client = ClarinetClient(
                base_url=self.api_base or settings.effective_api_base_url,
                service_token=settings.effective_service_token,
                verify_ssl=settings.api_verify_ssl,
            )
        return self._client
```

Note: the lazy import must reference the module attribute (`from clarinet.client import ClarinetClient` inside the property resolves at call time), so the monkeypatch on `clarinet.client.ClarinetClient` is honored.

- [ ] **Step 4: Run to verify pass**

Run: `./scripts/run_tests.sh tests/test_scripting.py -q > /tmp/test-scripting-t5b.txt 2>&1`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add clarinet/scripting.py tests/test_scripting.py
git commit -m "feat(scripting): lazy settings-based ClarinetClient on ScriptCtx"
```

---

## Task 6: @script decorator — app synthesis and async bridge (tasks.md 3.1 + 3.2)

**Files:**
- Modify: `clarinet/scripting.py`
- Modify: `tests/test_scripting.py`

**Interfaces:**
- Consumes: `ScriptCtx` (Tasks 3–5).
- Produces: `script() -> decorator`; decorated object is a `ScriptEntry` — directly callable (runs the CLI) and exposing `.app: typer.Typer` for `CliRunner`. Standard options: `--commit`, `--limit`, `--yes`, `--api-base`. Consumed by Task 7 (run semantics) and every downstream script.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scripting.py` (add `import typer`, `from typer.testing import CliRunner`, `from clarinet.scripting import script` to imports; add module-level `runner = CliRunner()` after the imports):

```python
def test_custom_option_passthrough() -> None:
    seen: dict[str, object] = {}

    @script()
    async def main(ctx: ScriptCtx, series: str | None = None) -> None:
        """Sample framed script."""
        seen["commit"] = ctx.commit
        seen["series"] = series

    result = runner.invoke(main.app, ["--series", "1.2.3"])
    assert result.exit_code == 0
    assert seen == {"commit": False, "series": "1.2.3"}


def test_standard_options_reach_ctx() -> None:
    seen: dict[str, object] = {}

    @script()
    async def main(ctx: ScriptCtx) -> None:
        """Sample."""
        seen.update(commit=ctx.commit, limit=ctx.limit, yes=ctx.yes, api_base=ctx.api_base)

    result = runner.invoke(
        main.app, ["--commit", "--limit", "5", "--yes", "--api-base", "http://x"]
    )
    assert result.exit_code == 0
    assert seen == {"commit": True, "limit": 5, "yes": True, "api_base": "http://x"}


def test_positional_argument_supported() -> None:
    seen: dict[str, object] = {}

    @script()
    async def main(ctx: ScriptCtx, xlsx: str) -> None:
        """With a required positional argument."""
        seen["xlsx"] = xlsx

    result = runner.invoke(main.app, ["cohort.xlsx"])
    assert result.exit_code == 0
    assert seen["xlsx"] == "cohort.xlsx"


def test_collision_raises_at_decoration_time() -> None:
    with pytest.raises(TypeError, match="commit"):

        @script()
        async def main(ctx: ScriptCtx, commit: bool = False) -> None:
            """Colliding parameter."""


def test_first_param_must_be_ctx() -> None:
    with pytest.raises(TypeError, match="ctx"):

        @script()
        async def main(series: str) -> None:
            """Missing ctx."""


def test_var_kwargs_rejected() -> None:
    with pytest.raises(TypeError, match="kwargs"):

        @script()
        async def main(ctx: ScriptCtx, **kwargs: str) -> None:
            """Var kwargs unsupported."""


def test_entry_is_callable_and_exposes_app() -> None:
    @script()
    async def main(ctx: ScriptCtx) -> None:
        """Sample."""

    assert callable(main)
    assert isinstance(main.app, typer.Typer)
```

- [ ] **Step 2: Run to verify failure**

Run: `./scripts/run_tests.sh tests/test_scripting.py -q > /tmp/test-scripting-t6a.txt 2>&1`
Expected: ImportError on `script`.

- [ ] **Step 3: Implement the decorator**

In `clarinet/scripting.py`, extend the imports (final stdlib set: `asyncio`, `inspect`, `sys`, `collections.Counter`, `collections.abc.Callable, Coroutine`, `typing.TYPE_CHECKING, Annotated, Any`) and append:

```python
type ScriptFn = Callable[..., Coroutine[Any, Any, None]]

_STANDARD_OPTIONS = ("commit", "limit", "yes", "api_base")


class ScriptEntry:
    """Callable CLI entry point; ``.app`` is exposed for CliRunner tests."""

    def __init__(self, app: typer.Typer) -> None:
        self.app = app

    def __call__(self) -> None:
        self.app()


def script() -> Callable[[ScriptFn], ScriptEntry]:
    """Wrap an async script body into a single-command typer app.

    The body's first parameter must be ``ctx: ScriptCtx``; its remaining
    parameters become CLI parameters (typer inference: no default -> argument,
    default -> option), merged with the standard options ``--commit``,
    ``--limit``, ``--yes``, ``--api-base``. See the module docstring for the
    target script shape.
    """

    def decorator(fn: ScriptFn) -> ScriptEntry:
        # eval_str resolves `from __future__ import annotations` strings in the
        # downstream script's namespace — typer needs real annotation objects.
        params = list(inspect.signature(fn, eval_str=True).parameters.values())
        if not params or params[0].name != "ctx":
            raise TypeError(
                f"@script function {fn.__name__!r} must take `ctx: ScriptCtx` "
                f"as its first parameter"
            )
        custom = params[1:]
        for param in custom:
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                raise TypeError(
                    f"@script does not support *args/**kwargs (parameter {param.name!r})"
                )
        collisions = sorted({p.name for p in custom} & set(_STANDARD_OPTIONS))
        if collisions:
            raise TypeError(
                f"@script parameter(s) collide with standard options: {collisions}"
            )

        standard = [
            inspect.Parameter(
                "commit",
                inspect.Parameter.KEYWORD_ONLY,
                default=False,
                annotation=Annotated[
                    bool, typer.Option("--commit", help="Actually write changes (default: dry-run).")
                ],
            ),
            inspect.Parameter(
                "limit",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=Annotated[
                    int | None,
                    typer.Option("--limit", help="Cap the run; the script decides what is counted."),
                ],
            ),
            inspect.Parameter(
                "yes",
                inspect.Parameter.KEYWORD_ONLY,
                default=False,
                annotation=Annotated[
                    bool, typer.Option("--yes", help="Skip interactive confirmation prompts.")
                ],
            ),
            inspect.Parameter(
                "api_base",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=Annotated[
                    str | None,
                    typer.Option("--api-base", help="Override the settings-derived API base URL."),
                ],
            ),
        ]

        def cli_body(**kwargs: Any) -> None:
            ctx = ScriptCtx(
                commit=kwargs.pop("commit"),
                limit=kwargs.pop("limit"),
                yes=kwargs.pop("yes"),
                api_base=kwargs.pop("api_base"),
            )
            asyncio.run(fn(ctx, **kwargs))

        cli_body.__signature__ = inspect.Signature([*custom, *standard])  # type: ignore[attr-defined]
        cli_body.__doc__ = fn.__doc__
        cli_body.__name__ = fn.__name__

        app = typer.Typer(add_completion=False)
        app.command()(cli_body)
        return ScriptEntry(app)

    return decorator
```

- [ ] **Step 4: Run to verify pass**

Run: `./scripts/run_tests.sh tests/test_scripting.py -q > /tmp/test-scripting-t6b.txt 2>&1`
Expected: 21 passed. If typer reports "Parameters without annotation are not allowed" or misreads unions, check that `eval_str=True` is present and the typer version is >= 0.15.

- [ ] **Step 5: Commit**

```bash
git add clarinet/scripting.py tests/test_scripting.py
git commit -m "feat(scripting): @script decorator - typer app synthesis and async bridge"
```

---

## Task 7: Run semantics — summary, hint, exit codes, help (tasks.md 3.3 + 3.4)

**Files:**
- Modify: `clarinet/scripting.py` (only `cli_body` inside `script()`)
- Modify: `tests/test_scripting.py`

**Interfaces:**
- Consumes: everything above.
- Produces: final frame contract — tally summary on stdout, dry-run hint, exit 0/1, exceptions propagate, `--help` lists all options.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scripting.py`:

```python
def test_summary_and_dry_run_hint() -> None:
    @script()
    async def main(ctx: ScriptCtx) -> None:
        """Sample."""
        ctx.tally.count("checked", 3)
        ctx.would("invalidate record 7")

    result = runner.invoke(main.app, [])
    assert result.exit_code == 0
    assert "[dry-run] would invalidate record 7" in result.output
    assert "checked: 3" in result.output
    assert "rerun with --commit" in result.output


def test_commit_run_has_no_hint() -> None:
    @script()
    async def main(ctx: ScriptCtx) -> None:
        """Sample."""
        ctx.tally.count("created")

    result = runner.invoke(main.app, ["--commit"])
    assert result.exit_code == 0
    assert "created: 1" in result.output
    assert "rerun with --commit" not in result.output


def test_failures_exit_one() -> None:
    @script()
    async def main(ctx: ScriptCtx) -> None:
        """Sample."""
        ctx.tally.fail("record 7", "API 500")

    result = runner.invoke(main.app, [])
    assert result.exit_code == 1
    assert "failed: 1" in result.output
    assert "record 7: API 500" in result.output


def test_uncaught_exception_propagates() -> None:
    @script()
    async def main(ctx: ScriptCtx) -> None:
        """Sample."""
        raise RuntimeError("boom")

    result = runner.invoke(main.app, [])
    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert "rerun with --commit" not in result.output  # no clean summary after a crash


def test_help_contract() -> None:
    @script()
    async def main(ctx: ScriptCtx, series: str | None = None) -> None:
        """Backfill sample for help test."""

    result = runner.invoke(main.app, ["--help"])
    assert result.exit_code == 0
    assert "Backfill sample for help test" in result.output
    for opt in ("--commit", "--limit", "--yes", "--api-base", "--series"):
        assert opt in result.output
    assert "token" not in result.output.lower()


def test_framed_run_never_builds_client_unasked(monkeypatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("ClarinetClient must not be constructed")

    monkeypatch.setattr(clarinet.client, "ClarinetClient", _boom)

    @script()
    async def main(ctx: ScriptCtx) -> None:
        """Filesystem-only script."""
        ctx.tally.count("checked")

    result = runner.invoke(main.app, [])
    assert result.exit_code == 0


def test_acceptance_dry_run_vs_commit() -> None:
    """End-to-end shape of a real backfill: iterate, gate writes, tally, limit."""
    written: list[int] = []

    @script()
    async def backfill(ctx: ScriptCtx, prefix: str = "rec") -> None:
        """Create missing reviews."""
        for record_id in (1, 2, 3, 4):
            if ctx.hit_limit(ctx.tally["created"]):
                break
            if not ctx.commit:
                ctx.would(f"create review for {prefix}-{record_id}")
                ctx.tally.count("created")
                continue
            written.append(record_id)
            ctx.tally.count("created")

    dry = runner.invoke(backfill.app, ["--limit", "2"])
    assert dry.exit_code == 0
    assert written == []
    assert dry.output.count("[dry-run] would") == 2
    assert "created: 2" in dry.output

    real = runner.invoke(backfill.app, ["--commit"])
    assert real.exit_code == 0
    assert written == [1, 2, 3, 4]
    assert "created: 4" in real.output
    assert "rerun with --commit" not in real.output
```

- [ ] **Step 2: Run to verify failure**

Run: `./scripts/run_tests.sh tests/test_scripting.py -q > /tmp/test-scripting-t7a.txt 2>&1`
Expected: the new summary/hint/exit-code tests fail (no summary printed, failures exit 0); earlier tests still pass.

- [ ] **Step 3: Extend cli_body**

Replace the `cli_body` function inside `script()` with:

```python
        def cli_body(**kwargs: Any) -> None:
            ctx = ScriptCtx(
                commit=kwargs.pop("commit"),
                limit=kwargs.pop("limit"),
                yes=kwargs.pop("yes"),
                api_base=kwargs.pop("api_base"),
            )
            # No blanket try/except: an escaping exception must stay visible
            # (traceback + non-zero exit), not be dressed up as a clean summary.
            asyncio.run(fn(ctx, **kwargs))
            typer.echo("")
            for line in ctx.tally.summary_lines():
                typer.echo(line)
            if not ctx.commit:
                typer.echo("DRY-RUN: rerun with --commit to write changes.")
            raise typer.Exit(code=1 if ctx.tally.failed else 0)
```

- [ ] **Step 4: Run to verify pass**

Run: `./scripts/run_tests.sh tests/test_scripting.py -q > /tmp/test-scripting-t7b.txt 2>&1`
Expected: 28 passed.

- [ ] **Step 5: Commit**

```bash
git add clarinet/scripting.py tests/test_scripting.py
git commit -m "feat(scripting): run summary, dry-run hint, and exit codes"
```

---

## Task 8: Agent-doc page (tasks.md 4.1)

**Files:**
- Create: `clarinet/docs/agent/claude/scripting.md`
- Modify: `clarinet/docs/agent/claude/overview.md` (directory-structure section)
- Modify: `tests/test_agent_scaffold.py:26` (`SECTION_RULES`)

**Interfaces:**
- Consumes: the frame contract from Tasks 3–7 (tables must match the shipped API exactly).
- Produces: payload page auto-installed downstream by `clarinet agent init/update`.

- [ ] **Step 1: Extend the payload guard**

In `tests/test_agent_scaffold.py`, change line 26:

```python
SECTION_RULES = ["definitions", "workflows", "slicer", "schemas", "utils", "scripting"]
```

- [ ] **Step 2: Run to verify failure**

Run: `./scripts/run_tests.sh tests/test_agent_scaffold.py -q > /tmp/test-scripting-t8a.txt 2>&1`
Expected: `test_payload_files_present` fails — `scripting.md` missing.

- [ ] **Step 3: Write the page**

Create `clarinet/docs/agent/claude/scripting.md`:

````markdown
---
paths:
  - "scripts/**"
---

# Operational scripts (`scripts/`) — the `clarinet.scripting` frame

One-shot operational scripts — backfills, repairs, remediations — live in the
project-root `scripts/` directory and run as standalone CLI processes
(`uv run python scripts/<name>.py`). Do not confuse them with `plan/scripts/`
(Slicer scene scripts) — those follow their own rules.

Every new script here uses the framework's `clarinet.scripting` frame instead
of hand-rolling argparse, client construction, counters, and exit codes.

## Target shape

```python
"""One-line purpose. Docstring of the FUNCTION becomes --help."""

from clarinet.scripting import script, ScriptCtx


@script()
async def main(ctx: ScriptCtx, series: str | None = None) -> None:
    """Invalidate finished create-nifty records whose volume file is missing."""
    async with ctx.client as client:
        checked = 0
        async for record in client.iter_records(
            record_type_name="create-nifty", record_status="finished"
        ):
            if ctx.hit_limit(checked):
                break
            checked += 1
            ctx.tally.count("checked")
            if series and record.series_uid != series:
                continue
            if not ctx.commit:
                ctx.would(f"invalidate record {record.id}")
                continue
            try:
                await client.invalidate_record(record.id, mode="hard", reason="...")
                ctx.tally.count("invalidated")
            except Exception as exc:  # per-item resilience
                ctx.tally.fail(f"record {record.id}", str(exc))


if __name__ == "__main__":
    main()
```

Custom parameters are plain typer-style function parameters: no default →
positional argument, with default → `--option`. The first parameter is always
`ctx: ScriptCtx`.

## Standard options (injected by @script)

| Option | Default | Meaning |
|---|---|---|
| `--commit` | off | Actually write. **Default is always dry-run** — never invert this. |
| `--limit N` | none | Cap the run. The script decides what N counts (checked/created/affected) via `ctx.hit_limit(count)`. |
| `--yes` | off | Pre-approve `ctx.confirm(...)` prompts (for non-interactive runs). |
| `--api-base URL` | none | Override `settings.effective_api_base_url`. The service token is settings/env only — never a flag. |

## ScriptCtx API

| Member | Contract |
|---|---|
| `ctx.commit` | Write gate. Branch on it around every mutation. |
| `ctx.limit` / `ctx.hit_limit(count)` | The frame supplies the value; apply it yourself. |
| `ctx.tally.count(key, n=1)` / `ctx.tally[key]` | Named counters, echoed as the final summary. |
| `ctx.tally.fail(item, detail)` | Per-item failure; any failure ⇒ exit code 1. |
| `ctx.would(msg)` | Uniform `[dry-run] would …` stdout line. |
| `ctx.confirm(msg)` | Interactive gate; honors `--yes`; refuses on non-TTY without `--yes`. |
| `ctx.client` | Lazy `ClarinetClient` from settings; enter with `async with ctx.client as client:`. Never touched ⇒ never constructed. |

## Conventions

- Operator-facing lines (would/summary/hints) go to stdout (`typer.echo` inside
  the frame); use `from clarinet.utils.logger import logger` for diagnostics.
- Exit codes: 0 clean, 1 when `ctx.tally.fail(...)` was recorded; uncaught
  exceptions crash loudly on purpose. Wrap per-item work in try/except +
  `tally.fail` when one bad item must not abort the run.
- The frame is deliberately frame-only: it never iterates, retries, or decides
  limit semantics for you. Do not grow loop helpers onto it.
- Test scripts in-process: `from typer.testing import CliRunner;`
  `CliRunner().invoke(main.app, ["--limit", "2"])`.
````

- [ ] **Step 4: Add the overview pointer**

In `clarinet/docs/agent/claude/overview.md`, directly after the line
"All files under `plan/` are imported as submodules …" paragraph, add:

```markdown
Operational one-shot scripts (backfills, repairs) live outside `plan/` in the
project-root `scripts/` directory and use the `clarinet.scripting` frame — see
`scripting.md`.
```

- [ ] **Step 5: Run to verify pass**

Run: `./scripts/run_tests.sh tests/test_agent_scaffold.py -q > /tmp/test-scripting-t8b.txt 2>&1`
Expected: all agent-scaffold tests pass (presence, token links, no-Cyrillic, init/update round-trip picks the page up automatically).

- [ ] **Step 6: Commit**

```bash
git add clarinet/docs/agent/claude/scripting.md clarinet/docs/agent/claude/overview.md tests/test_agent_scaffold.py
git commit -m "docs(agent): scripting-frame page for downstream script authors"
```

---

## Task 9: Full verification (tasks.md 5.1)

**Files:** none new — fixes only if checks fail.

- [ ] **Step 1: Lint + typecheck + format**

Run: `timeout 300 make check > /tmp/test-scripting-t9a.txt 2>&1`
Expected: format/lint/mypy all pass. If ruff rewrites files, re-Read them before any further Edit; commit fixes as `chore(scripting): make check fixes`.

- [ ] **Step 2: Full unit suite**

Run: `timeout 300 make test-unit > /tmp/test-scripting-t9b.txt 2>&1`
Expected: no regressions anywhere (the frame touches no existing module, so failures here mean an environment issue — investigate before blaming the change).

- [ ] **Step 3: Verify the summary numbers**

Read `/tmp/clarinet-test-report.json` summary (or the t9b log): `test_scripting.py` contributes 28 passed, `test_agent_scaffold.py` fully green.

- [ ] **Step 4: Commit (only if fixes were made)**

```bash
git add -u
git commit -m "chore(scripting): verification fixes"
```

---

## Spec coverage map (self-review)

| Spec requirement | Tasks |
|---|---|
| Standard script options | 3 (defaults), 6 (options), 7 (help, no token) |
| Custom parameters merge + collision | 6 |
| Async bridge and entry points | 6 |
| Limit supply, script-owned semantics | 3, 7 (acceptance) |
| Tally, summary, exit codes | 2, 7 |
| Uniform dry-run lines | 3, 7 |
| Confirmation gate | 4 |
| Lazy settings-based client | 5, 7 (framed run) |
| Downstream agent documentation | 8 |
