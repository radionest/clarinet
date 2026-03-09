"""Integration tests for Patient.auto_id auto-generation in PatientRepository."""

import pytest

from clarinet.models.patient import Patient
from clarinet.repositories.patient_repository import PatientRepository


@pytest.mark.asyncio
async def test_auto_id_assigned_when_none(test_session) -> None:
    """First patient without explicit auto_id gets auto_id=1."""
    repo = PatientRepository(test_session)
    patient = Patient(id="AUTO_PAT_001", name="Auto Patient")
    created = await repo.create(patient)

    assert created.auto_id == 1
    assert created.anon_id is not None


@pytest.mark.asyncio
async def test_auto_id_increments(test_session) -> None:
    """Second patient without explicit auto_id gets auto_id=2."""
    repo = PatientRepository(test_session)

    p1 = Patient(id="AUTO_PAT_001", name="First")
    await repo.create(p1)

    p2 = Patient(id="AUTO_PAT_002", name="Second")
    await repo.create(p2)

    assert p1.auto_id == 1
    assert p2.auto_id == 2


@pytest.mark.asyncio
async def test_explicit_auto_id_respected(test_session) -> None:
    """Explicit auto_id bypasses MAX logic (fast path via super().create())."""
    repo = PatientRepository(test_session)
    patient = Patient(id="EXPLICIT_PAT", name="Explicit", auto_id=42)
    created = await repo.create(patient)

    assert created.auto_id == 42


@pytest.mark.asyncio
async def test_auto_id_fills_gap(test_session) -> None:
    """Auto-assigned auto_id follows MAX, not sequential count."""
    repo = PatientRepository(test_session)

    # Create patient with explicit high auto_id
    p1 = Patient(id="GAP_PAT_001", name="High", auto_id=100)
    await repo.create(p1)

    # Next auto-assigned should be 101, not 2
    p2 = Patient(id="GAP_PAT_002", name="After Gap")
    await repo.create(p2)

    assert p2.auto_id == 101
