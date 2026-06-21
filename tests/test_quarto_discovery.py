"""Unit tests for clarinet.utils.quarto_discovery."""

from pathlib import Path

from clarinet.utils.quarto_discovery import (
    discover_quarto_templates,
    parse_quarto_metadata,
)

_FULL_QMD = """---
title: Monthly Summary
description: Records grouped by status
clarinet:
  data:
    - monthly_summary
    - user_stats
---

# Heading

```{python}
import pandas as pd
pd.read_csv("data/monthly_summary.csv")
```
"""


def test_parse_metadata_full() -> None:
    title, desc, data = parse_quarto_metadata(_FULL_QMD, fallback_name="monthly")
    assert title == "Monthly Summary"
    assert desc == "Records grouped by status"
    assert data == ["monthly_summary", "user_stats"]


def test_parse_metadata_falls_back_to_name_without_front_matter() -> None:
    qmd = "# Just markdown\n\nNo front matter here.\n"
    title, desc, data = parse_quarto_metadata(qmd, fallback_name="my_report")
    assert title == "my_report"
    assert desc == ""
    assert data == []


def test_parse_metadata_invalid_yaml_falls_back() -> None:
    qmd = "---\ntitle: [unterminated\n---\nbody\n"
    title, desc, data = parse_quarto_metadata(qmd, fallback_name="broken")
    assert title == "broken"
    assert desc == ""
    assert data == []


def test_parse_metadata_without_clarinet_data() -> None:
    qmd = "---\ntitle: Plain\n---\nbody\n"
    title, _desc, data = parse_quarto_metadata(qmd, fallback_name="x")
    assert title == "Plain"
    assert data == []


def test_parse_metadata_clarinet_without_data_key() -> None:
    qmd = "---\ntitle: T\nclarinet:\n  other: 1\n---\nbody\n"
    _title, _desc, data = parse_quarto_metadata(qmd, fallback_name="x")
    assert data == []


def test_discover_missing_folder_returns_empty(tmp_path: Path) -> None:
    assert discover_quarto_templates(tmp_path / "nope") == []


def test_discover_skips_non_qmd(tmp_path: Path) -> None:
    (tmp_path / "valid.qmd").write_text("---\ntitle: V\n---\nbody\n")
    (tmp_path / "report.sql").write_text("SELECT 1;")
    (tmp_path / "readme.md").write_text("ignored")
    items = discover_quarto_templates(tmp_path)
    assert [t.name for t, _ in items] == ["valid"]


def test_discover_loads_metadata_and_path(tmp_path: Path) -> None:
    (tmp_path / "alpha.qmd").write_text(_FULL_QMD)
    (tmp_path / "beta.qmd").write_text("# no front matter\n")
    items = discover_quarto_templates(tmp_path)
    by_name = {t.name: (t, p) for t, p in items}

    alpha, alpha_path = by_name["alpha"]
    assert alpha.title == "Monthly Summary"
    assert alpha.data_reports == ["monthly_summary", "user_stats"]
    assert alpha_path == (tmp_path / "alpha.qmd").resolve()

    beta, _ = by_name["beta"]
    assert beta.title == "beta"  # falls back to stem
    assert beta.description == ""
    assert beta.data_reports == []


def test_discover_results_are_sorted_by_stem(tmp_path: Path) -> None:
    for name in ["c", "a", "b"]:
        (tmp_path / f"{name}.qmd").write_text("body\n")
    items = discover_quarto_templates(tmp_path)
    assert [t.name for t, _ in items] == ["a", "b", "c"]


def test_template_kind_defaults_to_file() -> None:
    from clarinet.models.quarto_report import QuartoReportKind, QuartoReportTemplate

    t = QuartoReportTemplate(name="x", title="X", description="", data_reports=[])
    assert t.kind is QuartoReportKind.FILE
    book = QuartoReportTemplate(
        name="b", title="B", description="", data_reports=[], kind=QuartoReportKind.BOOK
    )
    assert book.kind is QuartoReportKind.BOOK
