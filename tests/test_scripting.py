"""Unit tests for clarinet.scripting — the @script frame for operational scripts."""

from __future__ import annotations

from clarinet.scripting import ScriptCtx, Tally


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
