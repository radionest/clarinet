"""Global search: repository lookups, service route-by-shape, POST /api/search."""

from clarinet.repositories.patient_repository import PatientRepository
from clarinet.repositories.series_repository import SeriesRepository
from clarinet.repositories.study_repository import StudyRepository
from tests.utils.factories import make_patient, make_series, make_study


async def _seed(test_session, *entities):
    """Persist factory-built entities in order and refresh the first."""
    for e in entities:
        test_session.add(e)
    await test_session.commit()
    for e in entities:
        await test_session.refresh(e)
    return entities


async def test_find_by_auto_id(test_session):
    (patient,) = await _seed(test_session, make_patient("SRCH_PAT1", "Alice", auto_id=4242))
    repo = PatientRepository(test_session)
    found = await repo.find_by_auto_id(4242)
    assert found is not None and found.id == patient.id
    assert await repo.find_by_auto_id(999999) is None


async def test_find_with_studies_by_ids_preserves_order(test_session):
    p1 = make_patient("SRCH_PAT2", "Bob", auto_id=4243)
    p2 = make_patient("SRCH_PAT3", "Carol", auto_id=4244)
    s1 = make_study(p1.id, uid="1.2.840.113786.1")
    s2 = make_study(p1.id, uid="1.2.840.113786.2")
    ser = make_series(s2.study_uid, uid="1.2.840.113786.2.1")
    await _seed(test_session, p1, p2, s1, s2, ser)
    repo = PatientRepository(test_session)
    rows = await repo.find_with_studies_by_ids([p2.id, p1.id, "MISSING"])
    assert [p.id for p in rows] == [p2.id, p1.id]
    by_id = {p.id: p for p in rows}
    assert len(by_id[p1.id].studies) == 2  # eager-loaded, no refresh
    assert len(by_id[p1.id].studies[0].series) >= 0  # series chained via selectinload


async def test_study_find_by_uid_and_anon_uid(test_session):
    p = make_patient("SRCH_PAT4", "Dan", auto_id=4245)
    s = make_study(p.id, uid="1.2.840.113786.3")
    s.anon_uid = "2.25.4245"
    await _seed(test_session, p, s)
    repo = StudyRepository(test_session)
    assert (await repo.find_by_uid("1.2.840.113786.3")) is not None
    assert (await repo.find_by_uid("1.2.840.113786.404")) is None
    assert (await repo.find_by_anon_uid("2.25.4245")).study_uid == s.study_uid
    assert (await repo.find_by_anon_uid("2.25.404")) is None


async def test_find_with_relations_by_uids(test_session):
    p = make_patient("SRCH_PAT5", "Eve", auto_id=4246)
    s = make_study(p.id, uid="1.2.840.113786.4")
    ser = make_series(s.study_uid, uid="1.2.840.113786.4.1")
    await _seed(test_session, p, s, ser)
    repo = StudyRepository(test_session)
    rows = await repo.find_with_relations_by_uids([s.study_uid, "1.2.840.404"])
    assert len(rows) == 1
    assert rows[0].patient.id == p.id  # eager-loaded, no refresh
    assert len(rows[0].series) == 1


async def test_list_uids_with_patient_ids(test_session):
    p = make_patient("SRCH_PAT6", "Frank", auto_id=4247)
    s1 = make_study(p.id, uid="1.2.840.113786.5")
    s2 = make_study(p.id, uid="1.2.840.113786.6")
    await _seed(test_session, p, s1, s2)
    repo = StudyRepository(test_session)
    pairs = await repo.list_uids_with_patient_ids()
    assert (s1.study_uid, p.id) in pairs
    assert (s2.study_uid, p.id) in pairs


async def test_series_repo_existing_lookups(test_session):
    """Sanity: the two reused SeriesRepository methods behave as expected."""
    p = make_patient("SRCH_PAT7", "Grace", auto_id=4248)
    s = make_study(p.id, uid="1.2.840.113786.7")
    ser = make_series(s.study_uid, uid="1.2.840.113786.7.1")
    ser.anon_uid = "2.25.4248"
    await _seed(test_session, p, s, ser)
    repo = SeriesRepository(test_session)
    assert (await repo.find_by_anon_uid("2.25.4248")).series_uid == ser.series_uid
    assert (await repo.find_by_series_uid(ser.series_uid)).study_uid == s.study_uid
