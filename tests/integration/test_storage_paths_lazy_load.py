"""Regression tests: storage_paths helpers must not lazy-load relationships in async context.

Uses ``fresh_session`` (empty identity map, simulates a production request) so
that any attempt to traverse ``Study.series`` from a non-eager-loaded Study
raises ``MissingGreenlet`` immediately — the shared ``test_session``'s
identity-map cache would otherwise mask such a bug.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import MissingGreenlet
from sqlmodel import select

from clarinet.models.patient import Patient
from clarinet.models.study import Series, Study
from clarinet.services.common.storage_paths import _modalities_string, build_context


@pytest.mark.asyncio
async def test_modalities_string_no_series_lazy_load(test_session, fresh_session):
    """``_modalities_string`` must read only ``study.modalities_in_study``.

    If a future contributor adds a fallback like
    ``"_".join(s.modality for s in study.series)`` when
    ``modalities_in_study`` is empty, the lazy-load on ``study.series`` will
    raise ``MissingGreenlet`` and this test will fail.
    """
    patient = Patient(id="LAZY_PAT", name="Lazy", auto_id=999)
    test_session.add(patient)
    study = Study(
        study_uid="1.2.3.lazy",
        patient_id=patient.id,
        date=datetime.now(UTC).date(),
        modalities_in_study=None,
    )
    test_session.add(study)
    series = Series(
        study_uid=study.study_uid,
        series_uid="1.2.3.lazy.1",
        series_number=1,
        modality="CT",
    )
    test_session.add(series)
    await test_session.commit()

    result = await fresh_session.execute(select(Study).where(Study.study_uid == study.study_uid))
    fresh_study = result.scalar_one()

    assert _modalities_string(fresh_study) == "unknown"

    with pytest.raises(MissingGreenlet):
        _ = fresh_study.series


@pytest.mark.asyncio
async def test_build_context_no_series_lazy_load(test_session, fresh_session):
    """``build_context`` reads ``study.modalities_in_study`` (column), not the relationship."""
    patient = Patient(id="LAZY_PAT2", name="L", auto_id=1000)
    test_session.add(patient)
    study = Study(
        study_uid="1.2.3.lazy2",
        patient_id=patient.id,
        date=datetime.now(UTC).date(),
        modalities_in_study="CT\\PT",
    )
    test_session.add(study)
    series = Series(
        study_uid=study.study_uid,
        series_uid="1.2.3.lazy2.1",
        series_number=1,
        modality="CT",
    )
    test_session.add(series)
    await test_session.commit()

    fresh_patient = (
        await fresh_session.execute(select(Patient).where(Patient.id == patient.id))
    ).scalar_one()
    fresh_study = (
        await fresh_session.execute(select(Study).where(Study.study_uid == study.study_uid))
    ).scalar_one()
    fresh_series = (
        await fresh_session.execute(select(Series).where(Series.series_uid == series.series_uid))
    ).scalar_one()

    # The fixtures do not run anonymization — this test is about lazy-load
    # behaviour, not about the anonymized-UID contract, so allow the
    # legacy fallback to populate the placeholders.
    ctx = build_context(
        patient=fresh_patient,
        study=fresh_study,
        series=fresh_series,
        fallback_to_unanonymized=True,
    )
    assert ctx["study_modalities"] == "CT_PT"

    with pytest.raises(MissingGreenlet):
        _ = fresh_study.series
