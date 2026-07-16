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
