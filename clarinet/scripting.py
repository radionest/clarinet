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

import asyncio
import inspect
import sys
from collections import Counter
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Annotated, Any

import typer

if TYPE_CHECKING:
    from clarinet.client import ClarinetClient


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
        self._client: ClarinetClient | None = None

    def hit_limit(self, count: int) -> bool:
        """True iff ``--limit`` was given and ``count`` reached it.

        What ``count`` counts (checked / created / affected) is the script's
        decision — the frame never applies the limit itself.
        """
        return self.limit is not None and count >= self.limit

    def would(self, msg: str) -> None:
        """Uniform dry-run line: ``[dry-run] would <msg>``."""
        typer.echo(f"[dry-run] would {msg}")

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
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"@script function {fn.__name__!r} must be `async def`")
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
            raise TypeError(f"@script parameter(s) collide with standard options: {collisions}")

        standard = [
            inspect.Parameter(
                "commit",
                inspect.Parameter.KEYWORD_ONLY,
                default=False,
                annotation=Annotated[
                    bool,
                    typer.Option("--commit", help="Actually write changes (default: dry-run)."),
                ],
            ),
            inspect.Parameter(
                "limit",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=Annotated[
                    int | None,
                    typer.Option(
                        "--limit", help="Cap the run; the script decides what is counted."
                    ),
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
            # No blanket try/except: an escaping exception must stay visible
            # (traceback + non-zero exit), not be dressed up as a clean summary.
            asyncio.run(fn(ctx, **kwargs))
            typer.echo("")
            for line in ctx.tally.summary_lines():
                typer.echo(line)
            if not ctx.commit:
                typer.echo("DRY-RUN: rerun with --commit to write changes.")
            raise typer.Exit(code=1 if ctx.tally.failed else 0)

        cli_body.__signature__ = inspect.Signature([*custom, *standard])  # type: ignore[attr-defined]
        cli_body.__doc__ = fn.__doc__
        cli_body.__name__ = fn.__name__

        app = typer.Typer(add_completion=False)
        app.command()(cli_body)
        return ScriptEntry(app)

    return decorator
