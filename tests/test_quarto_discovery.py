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


def test_template_kind_defaults_to_file() -> None:
    from clarinet.models.quarto_report import QuartoReportKind, QuartoReportTemplate

    t = QuartoReportTemplate(name="x", title="X", description="", data_reports=[])
    assert t.kind is QuartoReportKind.FILE
    book = QuartoReportTemplate(
        name="b", title="B", description="", data_reports=[], kind=QuartoReportKind.BOOK
    )
    assert book.kind is QuartoReportKind.BOOK


_BOOK_YML = """project:
  type: book
  output-dir: _site
book:
  title: Liver Book
  description: Multi-chapter liver report
clarinet:
  data:
    - liver_stats
"""


def test_parse_book_metadata_full() -> None:
    from clarinet.utils.quarto_discovery import parse_book_metadata

    title, desc, data, output_dir = parse_book_metadata(_BOOK_YML, fallback_name="liver")
    assert title == "Liver Book"
    assert desc == "Multi-chapter liver report"
    assert data == ["liver_stats"]
    assert output_dir == "_site"


def test_parse_book_metadata_defaults() -> None:
    from clarinet.utils.quarto_discovery import parse_book_metadata

    title, desc, data, output_dir = parse_book_metadata(
        "project:\n  type: book\n", fallback_name="b"
    )
    assert title == "b"
    assert desc == ""
    assert data == []
    assert output_dir == "_book"  # default when project.output-dir absent


def test_parse_book_metadata_invalid_yaml_falls_back() -> None:
    from clarinet.utils.quarto_discovery import parse_book_metadata

    title, desc, data, output_dir = parse_book_metadata("book:\n  title: [bad\n", fallback_name="b")
    assert (title, desc, data, output_dir) == ("b", "", [], "_book")


def test_discover_recognizes_book_subdir(tmp_path: Path) -> None:
    from clarinet.models.quarto_report import QuartoReportKind

    book = tmp_path / "report_book"
    book.mkdir()
    (book / "_quarto.yml").write_text(_BOOK_YML)
    (book / "index.qmd").write_text("# ch\n")
    (tmp_path / "single.qmd").write_text(_FULL_QMD)

    items = discover_quarto_templates(tmp_path)
    by_name = {t.name: (t, p) for t, p in items}

    book_t, book_path = by_name["report_book"]
    assert book_t.kind is QuartoReportKind.BOOK
    assert book_t.title == "Liver Book"
    assert book_t.data_reports == ["liver_stats"]
    assert book_path == book.resolve()  # path is the project dir, not a .qmd

    assert by_name["single"][0].kind is QuartoReportKind.FILE


def test_discover_ignores_non_book_subdir(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "img.png").write_text("x")
    (tmp_path / "ok.qmd").write_text("body\n")

    items = discover_quarto_templates(tmp_path)
    assert [t.name for t, _ in items] == ["ok"]


def test_discover_skips_book_with_non_utf8_quarto_yml(tmp_path: Path) -> None:
    """A _quarto.yml with invalid UTF-8 is skipped, not fatal to the whole scan."""
    bad = tmp_path / "badbook"
    bad.mkdir()
    (bad / "_quarto.yml").write_bytes(b"\xff\xfe not valid utf-8")
    (tmp_path / "ok.qmd").write_text("body\n")

    items = discover_quarto_templates(tmp_path)
    assert [t.name for t, _ in items] == ["ok"]
