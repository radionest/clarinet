"""Generate pandera DataFrame schemas from SQL report result types.

``clarinet quarto gen-types`` runs each ``*.sql`` report through
:meth:`~clarinet.repositories.report_repository.ReportRepository.describe_report`
(PostgreSQL result metadata, no row fetch) and renders one
:class:`pandera.DataFrameModel` per report into a single ``report_schemas.py``
module that the ``.qmd`` reports import.

``coerce=True`` repairs the dtype loss a CSV round-trip causes — string dates
become ``datetime64``, integers with NULLs stay ``Int64`` instead of ``float`` —
so report authors get both static column types (``df[Schema.col]`` is
typo-checked by mypy/pyright) and correct runtime dtypes from one artifact.

The rendered module imports only ``pandas``/``pandera`` (no DB, no clarinet), so
the renderer can copy it next to the ``.qmd`` and the sandboxed kernel can
import it without DB credentials.
"""

import keyword
import re
from collections.abc import Sequence

from clarinet.repositories.report_repository import ReportColumn

# PostgreSQL type name (asyncpg ``Type.name``) → pandera ``Series[...]`` text.
# Nullable extension dtypes by default: a projected column carries no NOT NULL
# guarantee, and a CSV round-trip introduces NA for empty cells — plain ``int``
# would silently widen to ``float`` (PR rationale: see docs/quarto-reports.md).
_PG_TO_PANDERA: dict[str, str] = {
    "bool": "Series[pd.BooleanDtype]",
    "int2": "Series[pd.Int64Dtype]",
    "int4": "Series[pd.Int64Dtype]",
    "int8": "Series[pd.Int64Dtype]",
    "float4": "Series[pd.Float64Dtype]",
    "float8": "Series[pd.Float64Dtype]",
    "numeric": "Series[pd.Float64Dtype]",
    "text": "Series[str]",
    "varchar": "Series[str]",
    "bpchar": "Series[str]",
    "char": "Series[str]",
    "name": "Series[str]",
    "uuid": "Series[str]",
    "date": "Series[pd.Timestamp]",
    "timestamp": "Series[pd.Timestamp]",
    "timestamptz": "Series[pd.Timestamp]",
}

# Unknown PG types (json, arrays, enums, custom domains) stay untyped rather than
# failing generation: the author still gets the column, just without coercion.
_FALLBACK = "Series[object]"

# One report's name + the columns describe_report() returned for it.
type ReportSpec = tuple[str, Sequence[ReportColumn]]

_MODULE_HEADER = '''\
"""Generated pandera schemas for SQL reports — DO NOT EDIT BY HAND.

Regenerate with ``clarinet quarto gen-types`` after changing a ``*.sql`` report.
Each class mirrors one report's columns; ``coerce=True`` fixes the dtypes a CSV
round-trip loses. Import the class you need in a ``.qmd`` python chunk::

    from report_schemas import {example}

    df = {example}.read()          # validated + dtype-coerced DataFrame
    df[{example}.{example_col}]     # typo-checked column reference
"""

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series
from pandera.typing.pandas import DataFrame
'''


def pandera_annotation(pg_type: str) -> str:
    """Map a PostgreSQL type name to a pandera ``Series[...]`` annotation.

    Unknown types fall back to ``Series[object]`` (no coercion) so a novel
    column type never breaks generation.
    """
    return _PG_TO_PANDERA.get(pg_type, _FALLBACK)


def class_name(report_name: str) -> str:
    """Turn a report stem (``monthly_summary``, ``user-stats``) into PascalCase."""
    parts = re.split(r"[^0-9a-zA-Z]+", report_name)
    name = "".join(p[:1].upper() + p[1:] for p in parts if p)
    if not name or not name[0].isalpha():
        name = f"Report{name}"
    return name


def _sanitize_field(column: str) -> str:
    """Coerce a SQL column name into a valid, non-keyword Python identifier."""
    candidate = column if column.isidentifier() else re.sub(r"\W|^(?=\d)", "_", column)
    if not candidate:
        candidate = "col"
    # Only hard keywords (class, def, ...) are illegal as field names; soft
    # keywords (type, match, case) are valid identifiers — leave them be.
    if keyword.iskeyword(candidate):
        candidate = f"{candidate}_"
    return candidate


def _unique(name: str, used: set[str]) -> str:
    """Disambiguate a field name against ones already emitted in the same class."""
    if name not in used:
        used.add(name)
        return name
    i = 2
    while f"{name}_{i}" in used:
        i += 1
    chosen = f"{name}_{i}"
    used.add(chosen)
    return chosen


def duplicate_column_names(columns: Sequence[ReportColumn]) -> list[str]:
    """Original column names the SQL projects more than once.

    ``pd.read_csv`` renames repeated headers (``id`` → ``id``, ``id.1``), so a
    generated ``alias`` pointing at the bare name cannot match the later copy —
    coercion silently skips it. The author should alias the column in SQL.
    """
    seen: set[str] = set()
    dups: list[str] = []
    for col in columns:
        if col.name in seen and col.name not in dups:
            dups.append(col.name)
        seen.add(col.name)
    return dups


def _render_class(report_name: str, columns: Sequence[ReportColumn]) -> str:
    cls = class_name(report_name)
    default_csv = f"data/{report_name}.csv"
    dups = set(duplicate_column_names(columns))
    lines = [f"class {cls}(pa.DataFrameModel):"]
    used: set[str] = set()
    if not columns:
        lines.append("    # report SQL projected no columns")
    for col in columns:
        field = _unique(_sanitize_field(col.name), used)
        args = ["nullable=True"]
        if field != col.name:
            args.append(f"alias={col.name!r}")
        line = f"    {field}: {pandera_annotation(col.pg_type)} = pa.Field({', '.join(args)})"
        if col.name in dups:
            line += f"  # duplicate column {col.name!r} — alias it in SQL"
        lines.append(line)
    lines += [
        "",
        "    class Config:",
        "        coerce = True",
        "        strict = False",
        "",
        "    @classmethod",
        f"    def read(cls, path: str = {default_csv!r}) -> DataFrame[{cls!r}]:",
        '        """Read the report CSV, validating and dtype-coercing the columns."""',
        "        return cls.validate(pd.read_csv(path))",
    ]
    return "\n".join(lines)


def render_schemas_module(reports: Sequence[ReportSpec]) -> str:
    """Render the full ``report_schemas.py`` source for all discovered reports."""
    if reports:
        first_name, first_cols = reports[0]
        example = class_name(first_name)
        example_col = _sanitize_field(first_cols[0].name) if first_cols else "column"
    else:
        example, example_col = "Report", "column"
    header = _MODULE_HEADER.format(example=example, example_col=example_col)
    classes = [_render_class(name, cols) for name, cols in reports]
    body = "\n\n\n".join(classes)
    return f"{header}\n\n{body}\n" if classes else header
