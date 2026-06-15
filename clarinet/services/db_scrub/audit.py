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


def collect_phi_terms(values: Iterable[str | None]) -> set[str]:
    """Build PHI search terms from original names and MRNs.

    Names are split on whitespace so ``"Ivanov Ivan"`` matches either token;
    everything is lower-cased and terms shorter than ``_MIN_TERM_LEN`` dropped.
    """
    terms: set[str] = set()
    for value in values:
        if not value:
            continue
        for token in re.split(r"\s+", value.strip()):
            normalized = token.strip().lower()
            if len(normalized) >= _MIN_TERM_LEN:
                terms.add(normalized)
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
