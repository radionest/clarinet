"""Unit tests for SeriesFilter."""

import pytest

from clarinet.services.dicom.models import SeriesResult
from clarinet.services.dicom.series_filter import (
    SeriesFilter,
    SeriesFilterCriteria,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_filter() -> SeriesFilter:
    """Filter with default excluded modalities, no min instance count."""
    return SeriesFilter(
        excluded_modalities=frozenset({"SR", "KO", "PR", "DOC"}),
        min_instance_count=None,
        unknown_modality_policy="include",
    )


def _criteria(
    series_uid: str = "1.2.3",
    modality: str | None = "CT",
    instance_count: int | None = 100,
    series_description: str | None = None,
) -> SeriesFilterCriteria:
    """Helper to build criteria."""
    return SeriesFilterCriteria(
        series_uid=series_uid,
        modality=modality,
        instance_count=instance_count,
        series_description=series_description,
    )


# ---------------------------------------------------------------------------
# Modality blocklist
# ---------------------------------------------------------------------------


class TestModalityBlocklist:
    """Tests for modality exclusion."""

    def test_excluded_modality_sr(self, default_filter: SeriesFilter) -> None:
        """SR modality is excluded."""
        result = default_filter.filter(
            ["sr_series"], to_criteria=lambda _: _criteria(modality="SR")
        )
        assert result.included == []
        assert len(result.excluded) == 1
        assert "excluded" in result.excluded[0].reason.lower()

    def test_excluded_modality_ko(self, default_filter: SeriesFilter) -> None:
        """KO modality is excluded."""
        result = default_filter.filter(
            ["ko_series"], to_criteria=lambda _: _criteria(modality="KO")
        )
        assert result.included == []
        assert len(result.excluded) == 1

    def test_included_modality_ct(self, default_filter: SeriesFilter) -> None:
        """CT modality passes filter."""
        result = default_filter.filter(
            ["ct_series"], to_criteria=lambda _: _criteria(modality="CT")
        )
        assert result.included == ["ct_series"]
        assert result.excluded == []

    def test_included_modality_mr(self, default_filter: SeriesFilter) -> None:
        """MR modality passes filter."""
        result = default_filter.filter(
            ["mr_series"], to_criteria=lambda _: _criteria(modality="MR")
        )
        assert result.included == ["mr_series"]
        assert result.excluded == []

    def test_case_insensitive(self, default_filter: SeriesFilter) -> None:
        """Modality check is case-insensitive."""
        result = default_filter.filter(["sr_lower"], to_criteria=lambda _: _criteria(modality="sr"))
        assert result.included == []
        assert len(result.excluded) == 1

    def test_case_insensitive_mixed(self, default_filter: SeriesFilter) -> None:
        """Mixed case modality is normalized."""
        result = default_filter.filter(["series"], to_criteria=lambda _: _criteria(modality="Sr"))
        assert result.included == []

    def test_modality_with_whitespace(self, default_filter: SeriesFilter) -> None:
        """Modality with whitespace is stripped."""
        result = default_filter.filter(["series"], to_criteria=lambda _: _criteria(modality=" SR "))
        assert result.included == []

    def test_custom_excluded_modalities(self) -> None:
        """Custom modality set overrides defaults."""
        f = SeriesFilter(
            excluded_modalities=frozenset({"SEG"}),
            unknown_modality_policy="include",
        )
        # SEG should be excluded
        result = f.filter(["s"], to_criteria=lambda _: _criteria(modality="SEG"))
        assert result.included == []
        # SR should pass (not in custom set)
        result = f.filter(["s"], to_criteria=lambda _: _criteria(modality="SR"))
        assert result.included == ["s"]


# ---------------------------------------------------------------------------
# Unknown modality policy
# ---------------------------------------------------------------------------


class TestUnknownModalityPolicy:
    """Tests for unknown (NULL) modality handling."""

    def test_include_by_default(self, default_filter: SeriesFilter) -> None:
        """NULL modality is included by default."""
        result = default_filter.filter(["unknown"], to_criteria=lambda _: _criteria(modality=None))
        assert result.included == ["unknown"]

    def test_exclude_when_policy_set(self) -> None:
        """NULL modality is excluded when policy is 'exclude'."""
        f = SeriesFilter(
            excluded_modalities=frozenset(),
            unknown_modality_policy="exclude",
        )
        result = f.filter(["unknown"], to_criteria=lambda _: _criteria(modality=None))
        assert result.included == []
        assert "NULL" in result.excluded[0].reason

    def test_empty_string_treated_as_unknown(self) -> None:
        """Empty string modality is treated same as NULL."""
        f = SeriesFilter(
            excluded_modalities=frozenset(),
            unknown_modality_policy="exclude",
        )
        result = f.filter(["empty"], to_criteria=lambda _: _criteria(modality=""))
        assert result.included == []


# ---------------------------------------------------------------------------
# Minimum instance count
# ---------------------------------------------------------------------------


class TestMinInstanceCount:
    """Tests for minimum instance count filtering."""

    def test_below_minimum(self) -> None:
        """Series below minimum instance count is excluded."""
        f = SeriesFilter(
            excluded_modalities=frozenset(),
            min_instance_count=5,
            unknown_modality_policy="include",
        )
        result = f.filter(["scout"], to_criteria=lambda _: _criteria(instance_count=2))
        assert result.included == []
        assert "below minimum" in result.excluded[0].reason.lower()

    def test_at_minimum(self) -> None:
        """Series at minimum instance count passes."""
        f = SeriesFilter(
            excluded_modalities=frozenset(),
            min_instance_count=5,
            unknown_modality_policy="include",
        )
        result = f.filter(["series"], to_criteria=lambda _: _criteria(instance_count=5))
        assert result.included == ["series"]

    def test_above_minimum(self) -> None:
        """Series above minimum instance count passes."""
        f = SeriesFilter(
            excluded_modalities=frozenset(),
            min_instance_count=5,
            unknown_modality_policy="include",
        )
        result = f.filter(["series"], to_criteria=lambda _: _criteria(instance_count=100))
        assert result.included == ["series"]

    def test_none_instance_count_passes(self) -> None:
        """NULL instance count is not filtered (no false excludes)."""
        f = SeriesFilter(
            excluded_modalities=frozenset(),
            min_instance_count=5,
            unknown_modality_policy="include",
        )
        result = f.filter(["series"], to_criteria=lambda _: _criteria(instance_count=None))
        assert result.included == ["series"]

    def test_no_minimum_configured(self, default_filter: SeriesFilter) -> None:
        """When min_instance_count is None, no filtering by count."""
        result = default_filter.filter(
            ["scout"], to_criteria=lambda _: _criteria(modality="CT", instance_count=1)
        )
        assert result.included == ["scout"]


# ---------------------------------------------------------------------------
# Mixed scenarios
# ---------------------------------------------------------------------------


class TestMixedFiltering:
    """Tests for filtering multiple series with different criteria."""

    def test_mixed_modalities(self) -> None:
        """Filter correctly partitions a mix of modalities."""
        f = SeriesFilter(
            excluded_modalities=frozenset({"SR", "KO"}),
            unknown_modality_policy="include",
        )
        items = [("CT", "1"), ("SR", "2"), ("MR", "3"), ("KO", "4"), ("PT", "5")]
        result = f.filter(
            items,
            to_criteria=lambda x: _criteria(series_uid=x[1], modality=x[0]),
        )
        assert len(result.included) == 3
        assert len(result.excluded) == 2
        included_modalities = {x[0] for x in result.included}
        assert included_modalities == {"CT", "MR", "PT"}

    def test_modality_and_instance_count_combined(self) -> None:
        """Both modality and instance count rules apply."""
        f = SeriesFilter(
            excluded_modalities=frozenset({"SR"}),
            min_instance_count=5,
            unknown_modality_policy="include",
        )
        items = [
            ("CT", 100),  # passes both
            ("SR", 100),  # fails modality
            ("MR", 2),  # fails instance count
            ("PT", 50),  # passes both
        ]
        result = f.filter(
            items,
            to_criteria=lambda x: _criteria(modality=x[0], instance_count=x[1]),
        )
        assert len(result.included) == 2
        assert len(result.excluded) == 2

    def test_empty_list(self, default_filter: SeriesFilter) -> None:
        """Empty input produces empty output."""
        result = default_filter.filter([], to_criteria=lambda _: _criteria())
        assert result.included == []
        assert result.excluded == []

    def test_all_excluded(self) -> None:
        """All series can be excluded."""
        f = SeriesFilter(
            excluded_modalities=frozenset({"SR", "KO"}),
            unknown_modality_policy="include",
        )
        items = ["sr1", "ko1"]
        criteria_map = {"sr1": "SR", "ko1": "KO"}
        result = f.filter(
            items,
            to_criteria=lambda x: _criteria(modality=criteria_map[x]),
        )
        assert result.included == []
        assert len(result.excluded) == 2

    def test_all_included(self, default_filter: SeriesFilter) -> None:
        """All series can pass."""
        items = ["ct1", "mr1"]
        criteria_map = {"ct1": "CT", "mr1": "MR"}
        result = default_filter.filter(
            items,
            to_criteria=lambda x: _criteria(modality=criteria_map[x]),
        )
        assert result.included == ["ct1", "mr1"]
        assert result.excluded == []


# ---------------------------------------------------------------------------
# SeriesFilterCriteria adapters
# ---------------------------------------------------------------------------


class TestCriteriaAdapters:
    """Tests for from_series_result and from_series adapters."""

    def test_from_series_result(self) -> None:
        """SeriesFilterCriteria.from_series_result maps fields correctly."""
        sr = SeriesResult(
            study_instance_uid="1.2.3",
            series_instance_uid="1.2.3.4",
            series_number=1,
            modality="CT",
            series_description="Axial",
            number_of_series_related_instances=120,
        )
        c = SeriesFilterCriteria.from_series_result(sr)
        assert c.series_uid == "1.2.3.4"
        assert c.modality == "CT"
        assert c.series_description == "Axial"
        assert c.instance_count == 120

    def test_from_series_result_nulls(self) -> None:
        """SeriesFilterCriteria.from_series_result handles NULL fields."""
        sr = SeriesResult(
            study_instance_uid="1.2.3",
            series_instance_uid="1.2.3.4",
        )
        c = SeriesFilterCriteria.from_series_result(sr)
        assert c.modality is None
        assert c.instance_count is None
        assert c.series_description is None

    def test_from_series(self) -> None:
        """SeriesFilterCriteria.from_series maps DB model fields correctly."""
        from clarinet.models.study import Series

        s = Series(
            series_uid="1.2.3.4",
            series_number=1,
            study_uid="1.2.3",
            modality="MR",
            instance_count=50,
            series_description="T1",
        )
        c = SeriesFilterCriteria.from_series(s)
        assert c.series_uid == "1.2.3.4"
        assert c.modality == "MR"
        assert c.instance_count == 50
        assert c.series_description == "T1"

    def test_from_series_nulls(self) -> None:
        """SeriesFilterCriteria.from_series handles NULL fields."""
        from clarinet.models.study import Series

        s = Series(
            series_uid="1.2.3.4",
            series_number=1,
            study_uid="1.2.3",
        )
        c = SeriesFilterCriteria.from_series(s)
        assert c.modality is None
        assert c.instance_count is None


# ---------------------------------------------------------------------------
# Reason messages
# ---------------------------------------------------------------------------


class TestReasonMessages:
    """Tests for exclusion reason messages."""

    def test_modality_reason_contains_modality(self, default_filter: SeriesFilter) -> None:
        """Exclusion reason for modality contains the modality name."""
        result = default_filter.filter(["s"], to_criteria=lambda _: _criteria(modality="SR"))
        assert "SR" in result.excluded[0].reason

    def test_instance_count_reason_contains_values(self) -> None:
        """Exclusion reason for instance count contains count and minimum."""
        f = SeriesFilter(
            excluded_modalities=frozenset(),
            min_instance_count=10,
            unknown_modality_policy="include",
        )
        result = f.filter(["s"], to_criteria=lambda _: _criteria(instance_count=3))
        assert "3" in result.excluded[0].reason
        assert "10" in result.excluded[0].reason

    def test_unknown_modality_reason(self) -> None:
        """Exclusion reason for unknown modality mentions NULL."""
        f = SeriesFilter(
            excluded_modalities=frozenset(),
            unknown_modality_policy="exclude",
        )
        result = f.filter(["s"], to_criteria=lambda _: _criteria(modality=None))
        assert "NULL" in result.excluded[0].reason
