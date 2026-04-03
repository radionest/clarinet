"""HTTP-backed repository adapters for background anonymization tasks.

These thin adapters implement the subset of repository methods that
``AnonymizationService`` actually calls, delegating to ``ClarinetClient``
instead of hitting the database directly.  This lets workers run without
DB credentials — they only need API access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clarinet.client import ClarinetClient
    from clarinet.models.patient import PatientRead
    from clarinet.models.study import StudyRead


class StudyRepoAdapter:
    """Adapts ClarinetClient to the StudyRepository interface used by AnonymizationService."""

    def __init__(self, client: ClarinetClient) -> None:
        self._client = client

    async def get_with_series(self, study_uid: str) -> StudyRead:
        """GET /studies/{uid} -- StudyRead already includes nested series."""
        return await self._client.get_study(study_uid)

    async def update_anon_uid(self, study: Any, anon_uid: str) -> Any:
        """POST /studies/{uid}/add_anonymized."""
        await self._client.add_anonymized_study_uid(study.study_uid, anon_uid)
        return study


class PatientRepoAdapter:
    """Adapts ClarinetClient to the PatientRepository interface used by AnonymizationService."""

    def __init__(self, client: ClarinetClient) -> None:
        self._client = client

    async def get(self, patient_id: str) -> PatientRead:
        """GET /patients/{id}."""
        return await self._client.get_patient(patient_id)


class SeriesRepoAdapter:
    """Adapts ClarinetClient to the SeriesRepository interface used by AnonymizationService."""

    def __init__(self, client: ClarinetClient) -> None:
        self._client = client

    async def update_anon_uid(self, series: Any, anon_uid: str) -> Any:
        """POST /series/{uid}/add_anonymized."""
        await self._client.add_anonymized_series_uid(series.series_uid, anon_uid)
        return series
