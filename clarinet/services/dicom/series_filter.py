"""Series filtering for DICOM import and anonymization.

Provides configurable rules to exclude non-image series (SR, KO, PR, etc.)
before processing. One shared filter logic, two application points:
import time (optional) and anonymization time (always).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, TypeVar

from clarinet.settings import settings

if TYPE_CHECKING:
    from clarinet.models.study import Series
    from clarinet.services.dicom.models import SeriesResult

T = TypeVar("T")

DEFAULT_EXCLUDED_MODALITIES: frozenset[str] = frozenset(
    {
        "SR",  # Structured Report
        "KO",  # Key Object Selection
        "PR",  # Presentation State
        "DOC",  # Encapsulated Document (PDF, CDA)
        "RTDOSE",  # Radiation Therapy Dose
        "RTPLAN",  # Radiation Therapy Plan
        "RTSTRUCT",  # RT Structure Set
        "REG",  # Registration
        "FID",  # Fiducials
        "RWV",  # Real World Value Mapping
    }
)


@dataclass(frozen=True)
class SeriesFilterCriteria:
    """Common DTO for filter evaluation — adapts both SeriesResult and Series."""

    series_uid: str
    modality: str | None = None
    series_description: str | None = None
    instance_count: int | None = None

    @classmethod
    def from_series_result(cls, sr: SeriesResult) -> Self:
        """Create from PACS C-FIND result (import time)."""
        return cls(
            series_uid=sr.series_instance_uid,
            modality=sr.modality,
            series_description=sr.series_description,
            instance_count=sr.number_of_series_related_instances,
        )

    @classmethod
    def from_series(cls, s: Series) -> Self:
        """Create from DB model (anonymization time)."""
        return cls(
            series_uid=s.series_uid,
            modality=s.modality,
            series_description=s.series_description,
            instance_count=s.instance_count,
        )


@dataclass(frozen=True)
class FilteredItem[T]:
    """An item that was evaluated and excluded by the filter."""

    item: T
    reason: str


@dataclass(frozen=True)
class SeriesFilterResult[T]:
    """Result of filtering — partitions into included/excluded."""

    included: list[T]
    excluded: list[FilteredItem[T]]


class SeriesFilter:
    """Filters series based on configurable rules.

    Rules applied in order:
    1. Modality check (blocklist)
    2. Unknown modality policy
    3. Excluded description patterns
    4. Minimum instance count
    """

    def __init__(
        self,
        excluded_modalities: frozenset[str] | None = None,
        min_instance_count: int | None = None,
        unknown_modality_policy: str | None = None,
        excluded_descriptions: list[str] | None = None,
    ):
        self.excluded_modalities = frozenset(
            m.upper() for m in (excluded_modalities or settings.series_filter_excluded_modalities)
        )
        self.min_instance_count = (
            min_instance_count
            if min_instance_count is not None
            else settings.series_filter_min_instance_count
        )
        self.unknown_modality_policy = (
            unknown_modality_policy
            if unknown_modality_policy is not None
            else settings.series_filter_unknown_modality_policy
        )
        patterns = (
            excluded_descriptions
            if excluded_descriptions is not None
            else settings.series_filter_excluded_descriptions
        )
        self._excluded_description_patterns: list[tuple[str, re.Pattern[str]]] = [
            (p, re.compile(p, re.IGNORECASE)) for p in patterns
        ]

    def filter(
        self, items: list[T], to_criteria: Callable[[T], SeriesFilterCriteria]
    ) -> SeriesFilterResult[T]:
        """Filter items using the provided criteria extractor.

        Args:
            items: List of items to filter
            to_criteria: Function to extract SeriesFilterCriteria from each item

        Returns:
            SeriesFilterResult with included and excluded partitions
        """
        included: list[T] = []
        excluded: list[FilteredItem[T]] = []
        for item in items:
            criteria = to_criteria(item)
            reason = self._evaluate(criteria)
            if reason:
                excluded.append(FilteredItem(item=item, reason=reason))
            else:
                included.append(item)
        return SeriesFilterResult(included=included, excluded=excluded)

    def _evaluate(self, c: SeriesFilterCriteria) -> str | None:
        """Returns exclusion reason, or None if series passes all rules."""
        modality = (c.modality or "").upper().strip()

        # Rule 1: modality blocklist
        if modality and modality in self.excluded_modalities:
            return f"Modality '{modality}' is excluded"

        # Rule 2: unknown modality
        if not modality and self.unknown_modality_policy == "exclude":
            return "Unknown modality (NULL)"

        # Rule 3: excluded description patterns
        if c.series_description is not None and self._excluded_description_patterns:
            for pattern_str, compiled in self._excluded_description_patterns:
                if compiled.search(c.series_description):
                    return (
                        f"Series description '{c.series_description}' "
                        f"matches excluded pattern '{pattern_str}'"
                    )

        # Rule 4: minimum instance count
        if (
            self.min_instance_count is not None
            and c.instance_count is not None
            and c.instance_count < self.min_instance_count
        ):
            return f"Instance count {c.instance_count} below minimum {self.min_instance_count}"

        return None
