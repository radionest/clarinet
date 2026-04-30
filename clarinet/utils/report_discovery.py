"""Discovery of custom SQL report templates from a project folder.

Reports are plain ``*.sql`` files placed in the project's reports folder
(``settings.reports_path``, default ``./review/``). The first comment block
provides optional metadata used in the admin UI:

::

    -- title: Monthly Summary
    -- description: Records grouped by status and record type.
    SELECT ...

When metadata is missing, ``title`` falls back to the file stem and
``description`` is empty.
"""

import re
from pathlib import Path

from clarinet.models.report import ReportTemplate
from clarinet.utils.logger import logger

_TITLE_RE = re.compile(r"^\s*--\s*title\s*:\s*(.+?)\s*$", re.IGNORECASE)
_DESCRIPTION_RE = re.compile(r"^\s*--\s*description\s*:\s*(.+?)\s*$", re.IGNORECASE)


# Pair of (template, sql) so the registry can keep both pieces without
# inventing a second representation that duplicates ReportTemplate's fields.
type DiscoveredReport = tuple[ReportTemplate, str]


def parse_report_metadata(sql_text: str, fallback_name: str) -> tuple[str, str]:
    """Extract ``title`` and ``description`` from leading SQL comments.

    Walks the file from the top, collecting metadata from ``-- key: value``
    lines, and stops at the first non-comment, non-blank line. Returns
    ``(title, description)``; ``title`` defaults to ``fallback_name`` and
    ``description`` to ``""``.
    """
    title = fallback_name
    description = ""
    for line in sql_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("--"):
            break
        if (m := _TITLE_RE.match(stripped)) is not None:
            title = m.group(1)
            continue
        if (m := _DESCRIPTION_RE.match(stripped)) is not None:
            description = m.group(1)
    return title, description


def discover_report_templates(folder: str | Path) -> list[DiscoveredReport]:
    """Scan ``folder`` for ``*.sql`` files and return ``(template, sql)`` pairs.

    A missing folder is not an error — the list is simply empty so the API
    can degrade gracefully when no reports are configured.
    """
    folder_path = Path(folder)
    if not folder_path.is_dir():
        logger.info(f"Reports folder {folder} does not exist; no reports loaded")
        return []

    discovered: list[DiscoveredReport] = []
    for path in sorted(folder_path.iterdir(), key=lambda p: p.stem):
        if not path.is_file() or path.suffix.lower() != ".sql":
            continue
        try:
            sql_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error(f"Failed to read report file {path}: {exc}")
            continue
        title, description = parse_report_metadata(sql_text, path.stem)
        template = ReportTemplate(name=path.stem, title=title, description=description)
        discovered.append((template, sql_text))
    return discovered
