import pytest
from pydantic import ValidationError

from clarinet.models.record_type import RecordTypeCreate


def test_unique_by_default_and_canonical():
    assert RecordTypeCreate(name="x", unique_by=["user", "parent"]).unique_by == frozenset(
        {"parent", "user"}
    )
    assert RecordTypeCreate(name="y").unique_by == frozenset({"user", "parent"})
    assert RecordTypeCreate(name="z", unique_by=None).unique_by is None


def test_empty_unique_by_rejected():
    with pytest.raises(ValidationError, match="max_records=1"):
        RecordTypeCreate(name="e", unique_by=set())


def test_shared_editing_requires_no_user_partition():
    with pytest.raises(ValidationError, match="shared_editing"):
        RecordTypeCreate(name="s", shared_editing=True, unique_by={"user"})


def test_legacy_key_translated_on_create():
    with pytest.warns(DeprecationWarning):
        rt = RecordTypeCreate(name="l", unique_per_user=False)
    assert rt.unique_by is None  # NOT silently ignored into the default


def test_legacy_key_translated_on_patch():
    from clarinet.models.record_type import RecordTypeOptional

    with pytest.warns(DeprecationWarning):
        p = RecordTypeOptional(unique_per_user=True)
    assert p.unique_by == frozenset({"user"}) and "unique_by" in p.model_fields_set
