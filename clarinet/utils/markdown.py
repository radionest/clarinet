"""Convert Markdown to sanitized HTML for safe display in the frontend.

Pipeline: ``markdown`` (Python-Markdown) renders source → ``nh3`` strips
everything outside the explicit tag/attribute/URL-scheme whitelist. The
whitelist is narrow on purpose — content typically comes from server
callbacks, but a malicious or buggy writer must not be able to inject
``<script>``, ``<iframe>``, event handlers, or ``javascript:`` URLs.
"""

from __future__ import annotations

import markdown as _md
import nh3

_ALLOWED_TAGS: set[str] = {
    "p",
    "ul",
    "ol",
    "li",
    "strong",
    "em",
    "code",
    "pre",
    "a",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "br",
    "hr",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}

_ALLOWED_ATTRS: dict[str, set[str]] = {"a": {"href", "title"}}

_ALLOWED_URL_SCHEMES: set[str] = {"http", "https", "mailto"}


def markdown_to_safe_html(text: str | None) -> str | None:
    """Render markdown to sanitized HTML.

    Returns ``None`` for ``None`` or empty input, and also when sanitization
    leaves the output empty (so frontends can simply skip rendering).
    """
    if not text:
        return None
    rendered = _md.markdown(text, extensions=["tables"])
    cleaned = nh3.clean(
        rendered,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes=_ALLOWED_URL_SCHEMES,
    )
    return cleaned or None
