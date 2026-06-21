"""Scaffolding for new Quarto reports (``clarinet quarto new``).

Symmetric to :mod:`clarinet.utils.quarto_discovery` (that module reads ``.qmd``
front matter; this one writes a fresh ``.qmd`` plus its sibling
``reference.docx`` style asset). Pure file/CLI logic — no DB, no app state.
"""

import yaml


def build_qmd_text(
    *,
    title: str,
    description: str,
    lang: str,
    formats: list[str],
    data_reports: list[str],
    reference_doc: str | None,
) -> str:
    """Render the full ``.qmd`` text: YAML front matter + one empty heading.

    ``reference_doc`` is emitted under ``format.docx.reference-doc`` only when
    given (and only when ``docx`` is in ``formats``). ``clarinet.data`` is
    omitted entirely when ``data_reports`` is empty. Serialized with
    ``allow_unicode`` so Cyrillic titles survive verbatim.
    """
    front_matter: dict[str, object] = {
        "title": title,
        "description": description,
        "lang": lang,
    }
    fmt_block: dict[str, object] = {}
    if "docx" in formats:
        docx_opts: dict[str, object] = {}
        if reference_doc:
            docx_opts["reference-doc"] = reference_doc
        fmt_block["docx"] = docx_opts
    if "pdf" in formats:
        fmt_block["pdf"] = {}
    if fmt_block:
        front_matter["format"] = fmt_block
    if data_reports:
        front_matter["clarinet"] = {"data": list(data_reports)}

    yaml_text = yaml.safe_dump(
        front_matter, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    return f"---\n{yaml_text}---\n\n# \n"
