"""The ``dicom`` xdist group must exist by the time xdist reads the markers.

xdist turns ``xdist_group`` marks into ``@group`` node-id suffixes in its own
``pytest_collection_modifyitems``. Its worker plugin is registered after
``tests/conftest.py`` is loaded and pytest calls hooks last-registered-first, so
a conftest hook without ``tryfirst`` adds the group too late: the DICOM suite
then spreads over workers and the send-to-PACS tests — same deterministic
anonymized UID — delete each other's study.
"""

import pytest
from pytest import PytestPluginManager

from tests import conftest


class _Item:
    def __init__(self, *marks: pytest.MarkDecorator) -> None:
        self.marks = [m.mark for m in marks]

    def iter_markers(self, name: str) -> list[pytest.Mark]:
        return [m for m in self.marks if m.name == name]

    def get_closest_marker(self, name: str) -> pytest.Mark | None:
        return next(iter(self.iter_markers(name)), None)

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.marks.append(marker.mark)


class _LateRegisteredReader:
    """Stands in for xdist's worker plugin: registered last, so called first.

    Reads the marks as ``xdist/remote.py`` does — every ``xdist_group`` mark,
    sorted and joined — so a test merged into two groups shows up as such.
    """

    def __init__(self) -> None:
        self.groups: list[str | None] = []

    def pytest_collection_modifyitems(self, items: list[_Item]) -> None:
        for item in items:
            names = sorted({str(m.args[0]) for m in item.iter_markers("xdist_group")})
            self.groups.append("_".join(names) or None)


def _groups_seen_by_late_plugin(*items: _Item) -> list[str | None]:
    pm = PytestPluginManager()
    pm.register(conftest)
    reader = _LateRegisteredReader()
    pm.register(reader)
    pm.hook.pytest_collection_modifyitems(session=None, config=None, items=list(items))
    return reader.groups


def test_dicom_tests_are_grouped_before_later_plugins_read_the_marks() -> None:
    assert _groups_seen_by_late_plugin(_Item(pytest.mark.dicom)) == ["dicom"]


def test_own_group_and_unmarked_tests_are_left_alone() -> None:
    slicer = _Item(pytest.mark.dicom, pytest.mark.xdist_group("slicer"))
    assert _groups_seen_by_late_plugin(slicer, _Item()) == ["slicer", None]
