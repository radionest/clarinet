"""Canonicalization for the RecordType ``unique_by`` uniqueness partitions."""

from collections.abc import Iterable

UNIQUE_BY_PARTITIONS: frozenset[str] = frozenset({"user", "parent"})
DEFAULT_UNIQUE_BY: frozenset[str] = frozenset({"user", "parent"})


def canonical_unique_by(value: Iterable[str] | bool | None) -> frozenset[str] | None:
    """``None``/``False`` → no uniqueness. A set/list → validated frozenset.

    ``False`` is the TOML spelling of off (TOML has no null). ``True`` and an
    empty iterable are rejected with teaching messages.
    """
    if value is None or value is False:
        return None
    if value is True:
        raise ValueError(
            "unique_by=true is not a partition set — use {'user'} for the legacy "
            "unique_per_user behavior, or omit the field for the default"
        )
    parts = frozenset(value)
    if not parts:
        raise ValueError(
            "unique_by cannot be empty — use None for no uniqueness, "
            "or max_records=1 for one-per-level"
        )
    unknown = parts - UNIQUE_BY_PARTITIONS
    if unknown:
        raise ValueError(f"unknown unique_by partition: {sorted(unknown)}")
    return parts


def legacy_unique_per_user(value: bool) -> frozenset[str] | None:
    """Deprecated ``unique_per_user`` mapping: ``True→{"user"}``, ``False→None``."""
    return frozenset({"user"}) if value else None
