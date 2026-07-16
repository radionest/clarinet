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

import sys
from collections import Counter
from typing import TYPE_CHECKING

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
