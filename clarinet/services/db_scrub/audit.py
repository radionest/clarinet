"""PHI-leak detection for the DB scrubber.

Defense-in-depth on top of schema-aware scrubbing: after the scrub, scan every
text/JSON value for the *known* PHI of the captured patients — their original
names and MRNs (DICOM PatientID), collected before mutation. A hit means a
free-text field still carries PHI the schema pass did not classify, so the run
must fail loudly rather than ship a leaky fixture.

JSON values are scanned at their string leaves only, so a preserved integer
(e.g. a lesion count) can never collide with a numeric MRN. Pure functions.
"""

import re
from collections.abc import Iterable, Iterator
from typing import Any

# Skip 1-2 character terms: too short to match as a meaningful whole word, and
# a frequent source of false positives (initials, units).
_MIN_TERM_LEN = 3


def collect_phi_terms(names: Iterable[str | None], ids: Iterable[str | None]) -> set[str]:
    """Build PHI search terms from original patient names and ids (MRNs).

    Names are split on whitespace so ``"Ivanov Ivan"`` matches either token, and
    tokens shorter than ``_MIN_TERM_LEN`` are dropped (short word fragments are
    noisy). Ids are operator-supplied MRNs and are included **verbatim,
    regardless of length** — a short MRN must still be auditable. The cost is a
    rare false positive when a short numeric MRN collides with a kept DICOM-UID
    segment, which fails safe (rollback; re-run with ``--allow-phi-leak`` if the
    hit is a known false positive). Everything is lower-cased.
    """
    terms: set[str] = set()
    for value in names:
        if not value:
            continue
        for token in re.split(r"\s+", value.strip()):
            normalized = token.strip().lower()
            if len(normalized) >= _MIN_TERM_LEN:
                terms.add(normalized)
    for value in ids:
        if value and value.strip():
            terms.add(value.strip().lower())
    return terms


def iter_strings(value: Any) -> Iterator[str]:
    """Yield every string leaf in a nested JSON value (dict / list / scalar)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)


def scan_text(text: str | None, terms: set[str]) -> set[str]:
    """Return the subset of ``terms`` occurring as whole words in ``text``.

    Whole-word matching (``\\b``) keeps a numeric MRN from matching inside an
    anon id (``CLARINET_12345`` — ``_`` is a word char, so there is no boundary
    before the digits) or an anon UID (``2.25.12345678``).
    """
    if not text or not terms:
        return set()
    low = text.lower()
    return {term for term in terms if re.search(rf"\b{re.escape(term)}\b", low)}


def scan_json(value: Any, terms: set[str]) -> set[str]:
    """Scan every string leaf of a JSON value, union the hits."""
    hits: set[str] = set()
    for leaf in iter_strings(value):
        hits |= scan_text(leaf, terms)
    return hits
