"""Unit tests for the pure post-submit editability verdict.

``is_record_editable`` computes "now" internally, so window cases are built
relative to call time with comfortable margins instead of exact boundaries.
"""

from datetime import UTC, datetime, timedelta

import pytest

from clarinet.models import RecordStatus, is_record_editable
from tests.utils.factories import make_record_type


def _ago(**kw) -> datetime:
    return datetime.now(UTC) - timedelta(**kw)


@pytest.mark.parametrize(
    "status",
    [RecordStatus.pending, RecordStatus.inwork, RecordStatus.blocked, RecordStatus.failed],
)
def test_non_finished_is_always_editable(status):
    """Nothing is submitted yet — even the strictest type does not lock."""
    rt = make_record_type(name="strict-rt", editable=False, edit_window_days=0)
    assert is_record_editable(status, _ago(days=10), rt) is True


def test_editable_false_locks_regardless_of_window():
    rt = make_record_type(name="locked-rt", editable=False, edit_window_days=365)
    assert is_record_editable(RecordStatus.finished, _ago(seconds=1), rt) is False


def test_no_window_keeps_finished_editable():
    rt = make_record_type(name="open-rt")
    assert is_record_editable(RecordStatus.finished, _ago(days=3650), rt) is True


@pytest.mark.parametrize("window_days", [None, 5])
def test_missing_finished_at_fails_open(window_days):
    """Legacy/imported rows without finished_at must not be locked forever."""
    rt = make_record_type(name="legacy-rt", edit_window_days=window_days)
    assert is_record_editable(RecordStatus.finished, None, rt) is True


def test_window_active_allows_edit():
    rt = make_record_type(name="window-rt", edit_window_days=5)
    assert is_record_editable(RecordStatus.finished, _ago(days=4), rt) is True


def test_window_expired_locks_edit():
    rt = make_record_type(name="window-rt", edit_window_days=5)
    assert is_record_editable(RecordStatus.finished, _ago(days=6), rt) is False


def test_zero_window_locks_at_submit():
    rt = make_record_type(name="zero-rt", edit_window_days=0)
    assert is_record_editable(RecordStatus.finished, _ago(seconds=1), rt) is False


@pytest.mark.parametrize(("days_ago", "expected"), [(4, True), (6, False)])
def test_naive_finished_at_treated_as_utc(days_ago, expected):
    """SQLite returns naive datetimes; the verdict must not raise or drift."""
    rt = make_record_type(name="naive-rt", edit_window_days=5)
    naive = (datetime.now(UTC) - timedelta(days=days_ago)).replace(tzinfo=None)
    assert is_record_editable(RecordStatus.finished, naive, rt) is expected
