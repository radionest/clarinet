"""The pure OUTPUT grid policy — every kind x action x repairable cell — and the
repair temp-path contract. No filesystem."""

from itertools import product
from pathlib import Path

import pytest

from clarinet.models.file_schema import GridMismatchAction
from clarinet.services.grid_policy import Verdict, _repair_tmp_path, decide
from clarinet.services.image.grid import RelationKind

SAME, REARRANGED, FOREIGN = RelationKind.SAME, RelationKind.REARRANGED, RelationKind.FOREIGN
PASS, REJECT, REPAIR, DELETE = Verdict.PASS, Verdict.REJECT, Verdict.REPAIR, Verdict.DELETE

ACTIONS: tuple[GridMismatchAction | None, ...] = (None, "reject", "conform", "delete")

# (action, kind, repairable, verdict) — the whole product written out by hand so
# that a cell can only change by editing its row.
ROWS: list[tuple[GridMismatchAction | None, RelationKind, bool, Verdict]] = [
    # SAME → PASS whatever was declared
    (None, SAME, False, PASS),
    (None, SAME, True, PASS),
    ("reject", SAME, False, PASS),
    ("reject", SAME, True, PASS),
    ("conform", SAME, False, PASS),
    ("conform", SAME, True, PASS),
    ("delete", SAME, False, PASS),
    ("delete", SAME, True, PASS),
    # REARRANGED — the only kind conform can repair, and only an 8-bit/layered mask
    (None, REARRANGED, False, REJECT),
    (None, REARRANGED, True, REJECT),
    ("reject", REARRANGED, False, REJECT),
    ("reject", REARRANGED, True, REJECT),
    ("conform", REARRANGED, False, REJECT),
    ("conform", REARRANGED, True, REPAIR),
    ("delete", REARRANGED, False, DELETE),
    ("delete", REARRANGED, True, DELETE),
    # FOREIGN — conform has nothing exact to offer, so it behaves like reject
    (None, FOREIGN, False, REJECT),
    (None, FOREIGN, True, REJECT),
    ("reject", FOREIGN, False, REJECT),
    ("reject", FOREIGN, True, REJECT),
    ("conform", FOREIGN, False, REJECT),
    ("conform", FOREIGN, True, REJECT),
    ("delete", FOREIGN, False, DELETE),
    ("delete", FOREIGN, True, DELETE),
]


@pytest.mark.parametrize(("action", "kind", "repairable", "expected"), ROWS)
def test_verdict(
    action: GridMismatchAction | None, kind: RelationKind, repairable: bool, expected: Verdict
) -> None:
    assert decide(action, kind, repairable=repairable).verdict is expected


def test_reason_names_the_8bit_rule_only_for_the_unrepairable_conform() -> None:
    assert {row[:3] for row in ROWS} == set(product(ACTIONS, RelationKind, (False, True)))
    for action, kind, repairable, _ in ROWS:
        reason = decide(action, kind, repairable=repairable).reason
        if (action, kind, repairable) == ("conform", REARRANGED, False):
            assert "8-bit" in reason
        else:
            assert reason == "", (action, kind, repairable)


@pytest.mark.parametrize("name", ["seg.nii", "seg.nii.gz", "lesion.seg.nrrd"])
def test_repair_tmp_path_is_unique_per_call_and_keeps_the_format_suffixes(name: str) -> None:
    """Two concurrent repairs of one record must not share a temp file — one
    request's re-check/replace window would otherwise pick up the other's
    partial rewrite — and the format probes test suffix membership, so the
    token has to sit before the original name, never after it.
    """
    subject = Path("/data/rec") / name
    first, second = _repair_tmp_path(subject), _repair_tmp_path(subject)
    assert first != second
    for tmp in (first, second):
        assert tmp.parent == subject.parent
        assert tmp.name.startswith(".repair.")
        assert tmp.suffixes[-len(subject.suffixes) :] == subject.suffixes
