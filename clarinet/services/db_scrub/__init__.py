"""Database anonymization for test-stand fixtures (``clarinet anon scrub-db``).

``DbScrubber`` narrows a restored copy of a live database to selected patients,
strips PHI (relational columns + JSON snapshots), rewrites the patient MRN to
the deterministic ``anon_id``, and audits the result for surviving PHI. See
:mod:`clarinet.services.db_scrub.scrubber`.
"""

from clarinet.services.db_scrub.audit import collect_phi_terms, scan_json, scan_text
from clarinet.services.db_scrub.json_scrub import scrub_record_data
from clarinet.services.db_scrub.scrubber import DbScrubber, PhiLeakError, ScrubReport

__all__ = [
    "DbScrubber",
    "PhiLeakError",
    "ScrubReport",
    "collect_phi_terms",
    "scan_json",
    "scan_text",
    "scrub_record_data",
]
