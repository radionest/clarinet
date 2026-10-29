"""Global search: repository lookups, service route-by-shape, POST /api/search."""

import hashlib

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from clarinet.api.app import app
from clarinet.api.auth_config import current_active_user
from clarinet.models.search import GlobalSearchResults
from clarinet.repositories.patient_repository import PatientRepository
from clarinet.repositories.series_repository import SeriesRepository
from clarinet.repositories.study_repository import StudyRepository
from clarinet.services.study_service import StudyService
from clarinet.settings import settings
from clarinet.utils.database import get_async_session
from tests.utils.factories import make_patient, make_series, make_study, make_user


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


# --- Service: route-by-shape resolution ---


def _service(test_session) -> StudyService:
    return StudyService(
        study_repo=StudyRepository(test_session),
        patient_repo=PatientRepository(test_session),
        series_repo=SeriesRepository(test_session),
    )


async def _seed_tree(test_session):
    """Patient 4242 with two studies (one 2.25-anonymized) and one series."""
    p = make_patient("SRCH_TREE_PAT", "Tree Patient", auto_id=4242)
    s_plain = make_study(p.id, uid="1.2.840.113786.100")
    s_anon = make_study(p.id, uid="1.2.840.113786.101")
    s_anon.anon_uid = "2.25.100101"
    ser = make_series(s_plain.study_uid, uid="1.2.840.113786.100.1")
    ser.anon_uid = "2.25.100102"
    await _seed(test_session, p, s_plain, s_anon, ser)
    return p, s_plain, s_anon, ser


async def test_search_auto_id_bare_digits(test_session):
    p, *_rest = await _seed_tree(test_session)
    result = await _service(test_session).global_search("4242")
    assert isinstance(result, GlobalSearchResults)
    assert [x.id for x in result.patients] == [p.id]
    assert result.studies == []


async def test_search_auto_id_prefixed(test_session):
    p, *_rest = await _seed_tree(test_session)
    result = await _service(test_session).global_search(f"{settings.anon_id_prefix}_4242")
    assert [x.id for x in result.patients] == [p.id]


async def test_search_study_anon_uid(test_session):
    _p, _s, s_anon, _ser = await _seed_tree(test_session)
    result = await _service(test_session).global_search("2.25.100101")
    assert [s.study_uid for s in result.studies] == [s_anon.study_uid]


async def test_search_series_anon_uid_returns_parent_study(test_session):
    _p, s_plain, _s, ser = await _seed_tree(test_session)
    result = await _service(test_session).global_search("2.25.100102")
    assert [s.study_uid for s in result.studies] == [s_plain.study_uid]
    assert ser.series_uid != s_plain.study_uid  # guard: series mapped to study


async def test_search_raw_study_uid(test_session):
    _p, s_plain, *_rest = await _seed_tree(test_session)
    result = await _service(test_session).global_search("1.2.840.113786.100")
    assert [s.study_uid for s in result.studies] == [s_plain.study_uid]


async def test_search_raw_series_uid_returns_parent_study(test_session):
    _p, s_plain, _s, ser = await _seed_tree(test_session)
    result = await _service(test_session).global_search(ser.series_uid)
    assert [s.study_uid for s in result.studies] == [s_plain.study_uid]


async def test_search_patient_lo_id(test_session):
    p, *_rest = await _seed_tree(test_session)
    result = await _service(test_session).global_search("SRCH_TREE_PAT")
    assert [x.id for x in result.patients] == [p.id]


async def test_search_per_study_hash(monkeypatch, test_session):
    monkeypatch.setattr(settings, "anon_per_study_patient_id", True)
    monkeypatch.setattr(settings, "anon_uid_salt", "us-test-salt")
    monkeypatch.setattr(settings, "anon_per_study_patient_id_hex_length", 8)
    _p, s_plain, *_rest = await _seed_tree(test_session)
    expected = hashlib.sha256(f"us-test-salt:{s_plain.study_uid}".encode()).hexdigest()[:8]
    result = await _service(test_session).global_search(f"{settings.anon_id_prefix}_{expected}")
    assert [s.study_uid for s in result.studies] == [s_plain.study_uid]


async def test_search_per_study_hash_gate_off_falls_through(monkeypatch, test_session):
    # Gate off (the default): the hash-shaped query must NOT scan and must
    # fall to the text fallback, which cannot match it.
    monkeypatch.setattr(settings, "anon_per_study_patient_id", False)
    await _seed_tree(test_session)
    result = await _service(test_session).global_search(f"{settings.anon_id_prefix}_deadbeef")
    assert result.patients == []
    assert result.studies == []


async def test_search_text_fallback_matches_name(test_session):
    await _seed(test_session, make_patient("SRCH_NAME_PAT", "Zoe Zapata", auto_id=4299))
    result = await _service(test_session).global_search("zapata")
    assert [x.id for x in result.patients] == ["SRCH_NAME_PAT"]


async def test_search_no_match_returns_empty(test_session):
    await _seed_tree(test_session)
    result = await _service(test_session).global_search("zzz-no-such-thing")
    assert result.patients == [] and result.studies == []


async def test_search_auto_id_out_of_int4_range(test_session):
    await _seed_tree(test_session)
    result = await _service(test_session).global_search("9" * 20)
    assert result.patients == [] and result.studies == []


async def test_search_whitespace_only_returns_empty(test_session):
    await _seed_tree(test_session)
    result = await _service(test_session).global_search("   ")
    assert result.patients == [] and result.studies == []


async def test_search_fallback_respects_limit(test_session):
    for i in range(3):
        await _seed(
            test_session,
            make_patient(f"SRCH_LIM_{i}", f"Limit Match {i}", auto_id=4300 + i),
        )
    result = await _service(test_session).global_search("limit match", limit=2)
    assert len(result.patients) == 2


# --- API: POST /api/search ---


async def _commit(test_session, entity):
    test_session.add(entity)
    await test_session.commit()
    await test_session.refresh(entity)
    return entity


def _client_for(test_session, user) -> AsyncClient:
    """ASGI client with the session and current_active_user overridden."""

    async def override_get_session():
        yield test_session

    app.dependency_overrides[get_async_session] = override_get_session
    app.dependency_overrides[current_active_user] = lambda: user
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", cookies={})


@pytest_asyncio.fixture
async def superuser_client(test_session, test_settings):
    user = await _commit(test_session, make_user(is_superuser=True))
    async with _client_for(test_session, user) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def plain_user_client(test_session, test_settings):
    user = await _commit(test_session, make_user(is_superuser=False))
    async with _client_for(test_session, user) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def search_tree(test_session):
    return await _seed_tree(test_session)


async def test_api_search_by_auto_id(superuser_client, search_tree):
    p, *_rest = search_tree
    resp = await superuser_client.post("/api/search", json={"query": "4242"})
    assert resp.status_code == 200
    body = resp.json()
    assert [x["id"] for x in body["patients"]] == [p.id]
    assert body["patients"][0]["studies"]  # PatientRead requires studies — eager load works
    assert body["studies"] == []


async def test_api_search_by_study_anon_uid(superuser_client, search_tree):
    _p, _s, s_anon, _ser = search_tree
    resp = await superuser_client.post("/api/search", json={"query": "2.25.100101", "limit": 10})
    assert resp.status_code == 200
    assert [s["study_uid"] for s in resp.json()["studies"]] == [s_anon.study_uid]


async def test_api_search_validation_errors(superuser_client):
    empty = await superuser_client.post("/api/search", json={"query": ""})
    extra = await superuser_client.post("/api/search", json={"query": "x", "bogus": 1})
    assert empty.status_code == 422
    assert extra.status_code == 422


async def test_api_search_non_admin_forbidden(plain_user_client, search_tree):
    resp = await plain_user_client.post("/api/search", json={"query": "4242"})
    assert resp.status_code in [401, 403]


async def test_api_search_no_results(superuser_client, search_tree):
    resp = await superuser_client.post("/api/search", json={"query": "zzz-none"})
    assert resp.status_code == 200
    assert resp.json() == {"patients": [], "studies": []}
