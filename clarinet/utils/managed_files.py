"""One owner of "is this file clarinet's", shared by every scaffolder.

A managed file carries a one-line header naming the clarinet version and the
command that refreshes it. The header is the first line, or the first line
after a leading YAML frontmatter block: a comment before ``---`` would stop
Claude's rules loader from reading ``paths:``. Each scaffolder keeps its own
policy (refresh, refuse, prune) and asks ``is_managed``. Seeds carry no header
and are written only when their target is absent.
"""

from pathlib import Path
from typing import Literal

from clarinet.utils.version import clarinet_version

# Prefix only, no version: a file written by an older clarinet is still managed.
_MARKERS = ("# managed by clarinet", "<!-- managed by clarinet")


def managed_header(comment: Literal["#", "<!--"], refresh_cmd: str) -> str:
    body = (
        f"managed by clarinet v{clarinet_version()} — do not edit; run '{refresh_cmd}' to refresh"
    )
    return f"# {body}\n" if comment == "#" else f"<!-- {body} -->\n"


def _header_offset(text: str) -> int:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return end + len("\n---\n")
    return 0


def with_header(text: str, header: str) -> str:
    at = _header_offset(text)
    return text[:at] + header + text[at:]


def strip_header(text: str) -> str:
    """Exact inverse of ``with_header``: drop only the header line at its position."""
    at = _header_offset(text)
    if not text.startswith(_MARKERS, at):
        return text
    end = text.find("\n", at)
    return text[:at] if end == -1 else text[:at] + text[end + 1 :]


def is_managed(path: Path) -> bool:
    """True iff the first line after optional frontmatter starts with the marker.

    Missing, unreadable or non-UTF-8 files are not clarinet's: ``False``, never raises.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return text.startswith(_MARKERS, _header_offset(text))
