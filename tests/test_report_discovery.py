"""Unit tests for clarinet.utils.report_discovery."""

from pathlib import Path

from clarinet.utils.report_discovery import (
    discover_report_templates,
    parse_report_metadata,
)


def test_parse_metadata_full() -> None:
    sql = (
        "-- title: Monthly Summary\n"
        "-- description: Records by status and type\n"
        "SELECT * FROM record;\n"
    )
    title, desc = parse_report_metadata(sql, fallback_name="monthly")
    assert title == "Monthly Summary"
    assert desc == "Records by status and type"


def test_parse_metadata_falls_back_to_name() -> None:
    sql = "SELECT 1;"
    title, desc = parse_report_metadata(sql, fallback_name="my_report")
    assert title == "my_report"
    assert desc == ""


def test_parse_metadata_stops_at_first_sql_line() -> None:
    """A title comment after the first SELECT must not override the fallback."""
    sql = "-- description: Quick lookup\nSELECT 1;\n-- title: should be ignored\n"
    title, desc = parse_report_metadata(sql, fallback_name="lookup")
    assert title == "lookup"
    assert desc == "Quick lookup"


def test_parse_metadata_is_case_insensitive() -> None:
    sql = "-- TITLE: Upper\n-- Description: Mixed\nSELECT 1;"
    title, desc = parse_report_metadata(sql, fallback_name="x")
    assert title == "Upper"
    assert desc == "Mixed"


def test_parse_metadata_skips_blank_lines() -> None:
    sql = "\n\n-- title: After blanks\nSELECT 1;"
    title, _ = parse_report_metadata(sql, fallback_name="x")
    assert title == "After blanks"


def test_discover_missing_folder_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    assert discover_report_templates(missing) == []


def test_discover_skips_non_sql(tmp_path: Path) -> None:
    (tmp_path / "valid.sql").write_text("SELECT 1;")
    (tmp_path / "readme.md").write_text("ignored")
    (tmp_path / "data.txt").write_text("ignored")
    items = discover_report_templates(tmp_path)
    assert [t.name for t, _ in items] == ["valid"]


def test_discover_loads_metadata_and_sql(tmp_path: Path) -> None:
    (tmp_path / "alpha.sql").write_text(
        "-- title: Alpha report\n-- description: First report\nSELECT id FROM record;\n"
    )
    (tmp_path / "beta.sql").write_text("SELECT 2;\n")
    items = discover_report_templates(tmp_path)
    by_name = {t.name: (t, sql) for t, sql in items}
    alpha_template, alpha_sql = by_name["alpha"]
    assert alpha_template.title == "Alpha report"
    assert alpha_template.description == "First report"
    assert "SELECT id FROM record" in alpha_sql
    beta_template, _ = by_name["beta"]
    assert beta_template.title == "beta"  # falls back to stem
    assert beta_template.description == ""


def test_discover_results_are_sorted_by_stem(tmp_path: Path) -> None:
    for name in ["c", "a", "b"]:
        (tmp_path / f"{name}.sql").write_text("SELECT 1;")
    items = discover_report_templates(tmp_path)
    assert [t.name for t, _ in items] == ["a", "b", "c"]
