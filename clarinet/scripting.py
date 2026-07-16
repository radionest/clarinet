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
