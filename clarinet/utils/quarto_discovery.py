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
      stage:
        - report_figures.py
        - ../plan/utils/seg_utils.py
    ---

``clarinet.stage`` is an optional list of files (paths relative to the .qmd) the
renderer stages flat into the sandbox render dir, so a chunk can import a project
helper module — and its non-sibling dependencies — alongside the .qmd.

``title`` falls back to the file stem, ``description`` to ``""`` and both the data
report list and the stage list to ``[]`` when the front matter is missing or
malformed.
"""

import re
from pathlib import Path
from typing import Any

import yaml

from clarinet.models.quarto_report import QuartoReportTemplate
from clarinet.utils.logger import logger

# Quarto front matter is a leading YAML block fenced by '---' lines. The
# optional BOM guard lets editors that prepend ﻿ still match.
_FRONT_MATTER_RE = re.compile(r"\A﻿?---[^\n]*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


# Pair of (template, path) so the registry keeps the on-disk location of each
# .qmd for the renderer, without inventing a second representation of metadata.
type DiscoveredQuartoReport = tuple[QuartoReportTemplate, Path]


def parse_quarto_metadata(
    qmd_text: str, fallback_name: str
) -> tuple[str, str, list[str], list[str]]:
    """Extract ``(title, description, data_reports, stage_files)`` from front matter.

    A missing or invalid front matter is not an error: ``title`` defaults to
    ``fallback_name``, ``description`` to ``""`` and both lists to ``[]``.
    """
    match = _FRONT_MATTER_RE.match(qmd_text)
    if match is None:
        return fallback_name, "", [], []
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        logger.warning(f"Invalid YAML front matter in Quarto report '{fallback_name}': {exc}")
        return fallback_name, "", [], []
    if not isinstance(meta, dict):
        return fallback_name, "", [], []

    title = str(meta.get("title") or fallback_name)
    description = str(meta.get("description") or "")
    return (
        title,
        description,
        _extract_clarinet_list(meta, "data"),
        _extract_clarinet_list(meta, "stage"),
    )


def _extract_clarinet_list(meta: dict[str, Any], key: str) -> list[str]:
    """Read the ``clarinet.<key>`` list of strings from front matter (``[]`` if absent)."""
    clarinet_meta = meta.get("clarinet")
    if not isinstance(clarinet_meta, dict):
        return []
    values = clarinet_meta.get(key)
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if item]


def discover_quarto_templates(folder: str | Path) -> list[DiscoveredQuartoReport]:
    """Scan ``folder`` for ``*.qmd`` files and return ``(template, path)`` pairs.

    A missing folder is not an error — the list is simply empty so the API can
    degrade gracefully when no Quarto reports are configured.
    """
    folder_path = Path(folder)
    if not folder_path.is_dir():
        logger.info(f"Quarto reports folder {folder} does not exist; no reports loaded")
        return []

    discovered: list[DiscoveredQuartoReport] = []
    for path in sorted(folder_path.iterdir(), key=lambda p: p.stem):
        if not path.is_file() or path.suffix.lower() != ".qmd":
            continue
        try:
            qmd_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error(f"Failed to read Quarto report file {path}: {exc}")
            continue
        title, description, data_reports, stage_files = parse_quarto_metadata(qmd_text, path.stem)
        template = QuartoReportTemplate(
            name=path.stem,
            title=title,
            description=description,
            data_reports=data_reports,
            stage_files=stage_files,
        )
        discovered.append((template, path.resolve()))
    return discovered
