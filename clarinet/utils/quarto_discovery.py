"""Discovery of Quarto report templates (``*.qmd``) from a project folder.

Quarto reports are ``*.qmd`` files placed in ``settings.quarto_reports_path``
(default ``./review/``, alongside the SQL ``*.sql`` reports). The leading YAML
front matter provides the metadata shown in the admin UI and declares which
SQL reports must be materialized as CSV before rendering::

    ---
    title: Monthly Summary
    description: Records grouped by status, rendered with charts.
    clarinet:
      data:
        - monthly_summary
        - user_stats
    ---

``title`` falls back to the file stem, ``description`` to ``""`` and the data
report list to ``[]`` when the front matter is missing or malformed.
"""

import re
from pathlib import Path
from typing import Any

import yaml

from clarinet.models.quarto_report import QuartoReportKind, QuartoReportTemplate
from clarinet.utils.logger import logger

# Quarto front matter is a leading YAML block fenced by '---' lines. The
# optional BOM guard lets editors that prepend ﻿ still match.
_FRONT_MATTER_RE = re.compile(r"\A﻿?---[^\n]*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


# Pair of (template, path) so the registry keeps the on-disk location of each
# .qmd for the renderer, without inventing a second representation of metadata.
type DiscoveredQuartoReport = tuple[QuartoReportTemplate, Path]


def parse_quarto_metadata(qmd_text: str, fallback_name: str) -> tuple[str, str, list[str]]:
    """Extract ``(title, description, data_reports)`` from a ``.qmd`` front matter.

    A missing or invalid front matter is not an error: ``title`` defaults to
    ``fallback_name``, ``description`` to ``""`` and ``data_reports`` to ``[]``.
    """
    match = _FRONT_MATTER_RE.match(qmd_text)
    if match is None:
        return fallback_name, "", []
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        logger.warning(f"Invalid YAML front matter in Quarto report '{fallback_name}': {exc}")
        return fallback_name, "", []
    if not isinstance(meta, dict):
        return fallback_name, "", []

    title = str(meta.get("title") or fallback_name)
    description = str(meta.get("description") or "")
    return title, description, _extract_data_reports(meta)


def _extract_data_reports(meta: dict[str, Any]) -> list[str]:
    """Read the ``clarinet.data`` list of SQL report names from front matter."""
    clarinet_meta = meta.get("clarinet")
    if not isinstance(clarinet_meta, dict):
        return []
    data = clarinet_meta.get("data")
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if item]


def parse_book_metadata(
    quarto_yml_text: str, fallback_name: str
) -> tuple[str, str, list[str], str]:
    """Extract ``(title, description, data_reports, output_dir)`` from a book's ``_quarto.yml``.

    Mirrors :func:`parse_quarto_metadata` for single ``.qmd`` files, reading a Quarto
    *book* project file instead: ``book.title`` / ``book.description`` for display, a
    top-level ``clarinet.data`` list for SQL reports to materialize, and
    ``project.output-dir`` (default ``_book``) for locating the rendered artifact. A
    missing/invalid YAML is not an error — title falls back to ``fallback_name``,
    description to ``""``, data to ``[]``, output-dir to ``_book``.
    """
    try:
        meta = yaml.safe_load(quarto_yml_text)
    except yaml.YAMLError as exc:
        logger.warning(f"Invalid YAML in book '{fallback_name}' _quarto.yml: {exc}")
        return fallback_name, "", [], "_book"
    if not isinstance(meta, dict):
        return fallback_name, "", [], "_book"

    book = meta.get("book")
    book = book if isinstance(book, dict) else {}
    title = str(book.get("title") or fallback_name)
    description = str(book.get("description") or "")

    project = meta.get("project")
    project = project if isinstance(project, dict) else {}
    output_dir = str(project.get("output-dir") or "_book")

    return title, description, _extract_data_reports(meta), output_dir


def discover_quarto_templates(folder: str | Path) -> list[DiscoveredQuartoReport]:
    """Scan ``folder`` for Quarto templates and return ``(template, path)`` pairs.

    Two kinds are discovered:

    * **FILE** — a top-level ``*.qmd`` file; ``name`` = file stem, path = the
      ``.qmd`` file itself.
    * **BOOK** — a top-level subdirectory that contains a ``_quarto.yml``; ``name``
      = directory name, path = the project directory.  Subdirectories without
      ``_quarto.yml`` are silently ignored.

    A missing folder is not an error — the list is simply empty so the API can
    degrade gracefully when no Quarto reports are configured.
    """
    folder_path = Path(folder)
    if not folder_path.is_dir():
        logger.info(f"Quarto reports folder {folder} does not exist; no reports loaded")
        return []

    discovered: list[DiscoveredQuartoReport] = []
    for path in sorted(folder_path.iterdir(), key=lambda p: p.stem):
        if path.is_dir():
            quarto_yml = path / "_quarto.yml"
            if not quarto_yml.is_file():
                continue
            try:
                yml_text = quarto_yml.read_text(encoding="utf-8")
            except OSError as exc:
                logger.error(f"Failed to read book _quarto.yml {quarto_yml}: {exc}")
                continue
            title, description, data_reports, _output_dir = parse_book_metadata(yml_text, path.name)
            discovered.append(
                (
                    QuartoReportTemplate(
                        name=path.name,
                        title=title,
                        description=description,
                        data_reports=data_reports,
                        kind=QuartoReportKind.BOOK,
                    ),
                    path.resolve(),
                )
            )
            continue
        if not path.is_file() or path.suffix.lower() != ".qmd":
            continue
        try:
            qmd_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error(f"Failed to read Quarto report file {path}: {exc}")
            continue
        title, description, data_reports = parse_quarto_metadata(qmd_text, path.stem)
        template = QuartoReportTemplate(
            name=path.stem,
            title=title,
            description=description,
            data_reports=data_reports,
        )
        discovered.append((template, path.resolve()))
    return discovered
