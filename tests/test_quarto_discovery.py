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
    title, desc, data, stage = parse_quarto_metadata(_FULL_QMD, fallback_name="monthly")
    assert title == "Monthly Summary"
    assert desc == "Records grouped by status"
    assert data == ["monthly_summary", "user_stats"]
    assert stage == []


def test_parse_metadata_falls_back_to_name_without_front_matter() -> None:
    qmd = "# Just markdown\n\nNo front matter here.\n"
    title, desc, data, stage = parse_quarto_metadata(qmd, fallback_name="my_report")
    assert title == "my_report"
    assert desc == ""
    assert data == []
    assert stage == []


def test_parse_metadata_invalid_yaml_falls_back() -> None:
    qmd = "---\ntitle: [unterminated\n---\nbody\n"
    title, desc, data, stage = parse_quarto_metadata(qmd, fallback_name="broken")
    assert title == "broken"
    assert desc == ""
    assert data == []
    assert stage == []


def test_parse_metadata_without_clarinet_data() -> None:
    qmd = "---\ntitle: Plain\n---\nbody\n"
    title, _desc, data, _stage = parse_quarto_metadata(qmd, fallback_name="x")
    assert title == "Plain"
    assert data == []


def test_parse_metadata_clarinet_without_data_key() -> None:
    qmd = "---\ntitle: T\nclarinet:\n  other: 1\n---\nbody\n"
    _title, _desc, data, _stage = parse_quarto_metadata(qmd, fallback_name="x")
    assert data == []


def test_parse_metadata_stage_files() -> None:
    qmd = (
        "---\ntitle: T\nclarinet:\n  stage:\n    - report_figures.py\n"
        "    - ../plan/utils/seg_utils.py\n---\nbody\n"
    )
    _title, _desc, _data, stage = parse_quarto_metadata(qmd, fallback_name="x")
    assert stage == ["report_figures.py", "../plan/utils/seg_utils.py"]


def test_parse_metadata_stage_not_a_list_ignored() -> None:
    qmd = "---\ntitle: T\nclarinet:\n  stage: report_figures.py\n---\nbody\n"
    _title, _desc, _data, stage = parse_quarto_metadata(qmd, fallback_name="x")
    assert stage == []


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
    assert alpha.stage_files == []
    assert alpha_path == (tmp_path / "alpha.qmd").resolve()

    beta, _ = by_name["beta"]
    assert beta.title == "beta"  # falls back to stem
    assert beta.description == ""
    assert beta.data_reports == []
    assert beta.stage_files == []


def test_discover_propagates_stage_files(tmp_path: Path) -> None:
    (tmp_path / "fig.qmd").write_text(
        "---\ntitle: Fig\nclarinet:\n  stage:\n    - report_figures.py\n---\nbody\n"
    )
    items = discover_quarto_templates(tmp_path)
    assert len(items) == 1
    template, _path = items[0]
    assert template.stage_files == ["report_figures.py"]


def test_discover_results_are_sorted_by_stem(tmp_path: Path) -> None:
    for name in ["c", "a", "b"]:
        (tmp_path / f"{name}.qmd").write_text("body\n")
    items = discover_quarto_templates(tmp_path)
    assert [t.name for t, _ in items] == ["a", "b", "c"]
