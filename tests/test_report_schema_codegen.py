"""Unit tests for pandera schema codegen from SQL report column types."""

import importlib.util
import sys
from pathlib import Path

import pytest

from clarinet.repositories.report_repository import ReportColumn
from clarinet.utils.report_schema_codegen import (
    class_name,
    duplicate_column_names,
    pandera_annotation,
    render_schemas_module,
)


@pytest.mark.parametrize(
    ("pg_type", "expected"),
    [
        ("int2", "Series[pd.Int64Dtype]"),
        ("int4", "Series[pd.Int64Dtype]"),
        ("int8", "Series[pd.Int64Dtype]"),
        ("bool", "Series[pd.BooleanDtype]"),
        ("text", "Series[str]"),
        ("varchar", "Series[str]"),
        ("uuid", "Series[str]"),
        ("date", "Series[pd.Timestamp]"),
        ("timestamptz", "Series[pd.Timestamp]"),
        ("float8", "Series[pd.Float64Dtype]"),
        ("numeric", "Series[pd.Float64Dtype]"),
        ("jsonb", "Series[object]"),  # unknown → fallback, no coercion
        ("_int4", "Series[object]"),  # array → fallback
    ],
)
def test_pandera_annotation(pg_type: str, expected: str) -> None:
    assert pandera_annotation(pg_type) == expected


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("monthly_summary", "MonthlySummary"),
        ("user-stats", "UserStats"),
        ("demo_records", "DemoRecords"),
        ("2024_q1", "Report2024Q1"),  # leading digit → Report-prefixed
        ("report", "Report"),
    ],
)
def test_class_name(stem: str, expected: str) -> None:
    assert class_name(stem) == expected


def test_render_module_sanitizes_and_aliases() -> None:
    """Non-identifier / keyword / duplicate columns get safe field names + alias."""
    cols = [
        ReportColumn("total amount", "numeric"),
        ReportColumn("class", "text"),  # python hard keyword
        ReportColumn("id", "int4"),
        ReportColumn("id", "int4"),  # duplicate column name
    ]
    module = render_schemas_module([("weird", cols)])

    assert (
        "total_amount: Series[pd.Float64Dtype] = pa.Field(nullable=True, alias='total amount')"
        in module
    )
    assert "class_: Series[str] = pa.Field(nullable=True, alias='class')" in module
    assert "id: Series[pd.Int64Dtype] = pa.Field(nullable=True)" in module
    assert "id_2: Series[pd.Int64Dtype] = pa.Field(nullable=True, alias='id')" in module
    # The duplicate 'id' column is flagged inline (pandas reads it as 'id.1').
    assert "# duplicate column 'id' — alias it in SQL" in module


def test_duplicate_column_names() -> None:
    cols = [ReportColumn("id", "int4"), ReportColumn("id", "int4"), ReportColumn("x", "text")]
    assert duplicate_column_names(cols) == ["id"]
    assert duplicate_column_names([ReportColumn("a", "int4"), ReportColumn("b", "text")]) == []


def test_render_empty_module_is_valid_python() -> None:
    module = render_schemas_module([])
    assert "import pandera.pandas as pa" in module
    compile(module, "report_schemas.py", "exec")  # no classes, still valid


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="importing pandas triggers _strptime LocaleTime → 'unknown locale: en-US' on the "
    "Windows CI runner; the report kernel that imports the generated module runs on Linux only",
)
def test_generated_module_coerces_csv_dtypes(tmp_path: Path) -> None:
    """End-to-end: the generated schema reads a CSV with correct, coerced dtypes.

    A plain ``read_csv`` would type ``id`` as float64 (NULL present) and
    ``created_at`` as a string; pandera coercion restores Int64 / datetime.
    """
    cols = [
        ReportColumn("id", "int4"),
        ReportColumn("email", "text"),
        ReportColumn("is_active", "bool"),
        ReportColumn("created_at", "timestamptz"),
    ]
    module_path = tmp_path / "report_schemas.py"
    module_path.write_text(render_schemas_module([("demo_records", cols)]), encoding="utf-8")

    csv = tmp_path / "data" / "demo_records.csv"
    csv.parent.mkdir()
    csv.write_text(
        "id,email,is_active,created_at\n"
        "1,a@x.io,true,2026-01-02 10:00:00\n"
        ",b@x.io,false,2026-03-04 12:30:00\n",
        encoding="utf-8",
    )

    spec = importlib.util.spec_from_file_location("generated_report_schemas", module_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    df = mod.DemoRecords.read(str(csv))
    assert str(df["id"].dtype) == "Int64"
    assert str(df["is_active"].dtype) == "boolean"
    assert str(df["created_at"].dtype) == "datetime64[ns]"
    # The schema attribute resolves to the column name (typo-safe subscripting).
    assert mod.DemoRecords.email == "email"
    assert df[mod.DemoRecords.email].iloc[0] == "a@x.io"
