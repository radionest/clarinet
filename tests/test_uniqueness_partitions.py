import pytest

from clarinet.models.uniqueness import (
    DEFAULT_UNIQUE_BY,
    canonical_unique_by,
    legacy_unique_per_user,
)


def test_order_and_dupe_insensitive():
    assert canonical_unique_by(["user", "parent", "user"]) == frozenset({"parent", "user"})


def test_none_is_off():
    assert canonical_unique_by(None) is None


def test_false_is_toml_off():
    assert canonical_unique_by(False) is None


def test_true_rejected_with_teaching_message():
    with pytest.raises(ValueError, match="user"):
        canonical_unique_by(True)


def test_default_is_user_parent():
    assert frozenset({"user", "parent"}) == DEFAULT_UNIQUE_BY


def test_empty_rejected_with_teaching_message():
    with pytest.raises(ValueError, match=r"None.*max_records=1|max_records=1.*None"):
        canonical_unique_by(set())


def test_unknown_token_rejected():
    with pytest.raises(ValueError, match="level"):
        canonical_unique_by({"level"})


def test_legacy_mapping():
    assert legacy_unique_per_user(True) == frozenset({"user"})
    assert legacy_unique_per_user(False) is None
