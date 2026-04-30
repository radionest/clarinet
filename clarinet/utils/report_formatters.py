"""Serializers turning SQL result rows into CSV or XLSX bytes.

Both writers accept the same shape — ``columns: list[str]`` plus
``rows: Sequence[Sequence[Any]]`` — so the calling service does not need
format-specific branches when building the response.
"""

import csv
import io
from collections.abc import Sequence
from typing import Any


def to_csv(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> io.BytesIO:
    """Build a UTF-8 CSV with a BOM so Excel opens Cyrillic files correctly.

    ``\\r\\n`` line terminators match the dialect Excel produces — using ``\\n``
    here causes Excel to merge rows when re-saving.
    """
    text_buf = io.StringIO()
    writer = csv.writer(text_buf, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    data = text_buf.getvalue().encode("utf-8-sig")
    return io.BytesIO(data)


def to_xlsx(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> io.BytesIO:
    """Build an XLSX workbook in memory using openpyxl in write-only mode.

    Write-only mode streams rows to the underlying zip without keeping the
    whole sheet in memory, so reasonably large reports stay flat in RSS.
    """
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title="Report")
    ws.append(list(columns))
    for row in rows:
        ws.append([_cell_value(v) for v in row])
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out


def _cell_value(value: Any) -> Any:
    """Coerce DB values to types openpyxl can serialize without warnings."""
    if value is None:
        return None
    if isinstance(value, str | int | float | bool):
        return value
    return str(value)
