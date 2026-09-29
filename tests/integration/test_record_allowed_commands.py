"""RecordRead.allowed_commands: the viewer's commands, decided by the lifecycle policy."""

import pytest

from clarinet.models import Record
from clarinet.models import RecordStatus as S
from clarinet.models.record import RecordRead
from tests.utils.lifecycle import client_as
from tests.utils.urls import ADMIN_RECORDS, RECORDS_BASE, RECORDS_FIND

_MINIMAL_RECORD_READ = {
    "id": 1,
    "patient_id": "p1",
    "record_type_name": "abcde",
    "patient": {"id": "p1"},
    "record_type": {"name": "abcde"},
}


def test_allowed_commands_drops_names_unknown_to_this_server_version():
    """A newer server may report a command this client's RecordCommandName Literal
    doesn't know about yet. Parsing must drop it, not fail the whole record."""
    read = RecordRead.model_validate(
        {**_MINIMAL_RECORD_READ, "allowed_commands": ["claim", "future_cmd"]}
    )
    assert read.allowed_commands == ["claim"]


@pytest.mark.asyncio
async def test_a_locked_record_for_its_owner_and_for_an_admin(lc, test_settings):
    rt = await lc.record_type("lc-ac-lock", editable=False)
    rec = await lc.seed(rt, status=S.finished, user_id=lc.owner.id)
    async with client_as(lc.owner, lc.session, test_settings) as client:
        owner_view = (await client.get(f"{RECORDS_BASE}/{rec.id}")).json()
    async with client_as(lc.admin, lc.session, test_settings) as client:
        admin_view = (await client.get(f"{RECORDS_BASE}/{rec.id}")).json()
    assert not {"edit", "restart"} & set(owner_view["allowed_commands"])
    assert {"edit", "restart"} <= set(admin_view["allowed_commands"])


@pytest.mark.asyncio
@pytest.mark.parametrize("releasable", [False, True])
async def test_release_follows_the_type_flag(lc, test_settings, releasable):
    rt = await lc.record_type(f"lc-ac-rel-{int(releasable)}", releasable=releasable)
    rec = await lc.seed(rt, status=S.inwork, user_id=lc.owner.id)
    async with client_as(lc.owner, lc.session, test_settings) as client:
        view = (await client.get(f"{RECORDS_BASE}/{rec.id}")).json()
    assert ("unassign" in view["allowed_commands"]) is releasable


@pytest.mark.asyncio
async def test_admin_endpoints_carry_allowed_commands(lc, test_settings):
    rec = await lc.seed(await lc.record_type("lc-ac-admin"), status=S.pending)
    async with client_as(lc.admin, lc.session, test_settings) as client:
        resp = await client.patch(
            f"{ADMIN_RECORDS}/{rec.id}/assign", params={"user_id": str(lc.owner.id)}
        )
    assert resp.status_code == 200
    assert "unassign" in resp.json()["allowed_commands"]


@pytest.mark.asyncio
async def test_find_survives_a_record_whose_status_is_a_raw_str(lc, test_settings):
    """Fix round 1: a Record built with ``status="pending"`` (a plain str, not
    ``RecordStatus.pending``) must not 500 when the find path computes
    ``allowed_commands`` for a non-admin viewer.

    SQLModel ``table=True`` classes skip pydantic coercion on direct construction, so
    a ``Record`` built this way (as several existing test fixtures do, e.g.
    ``tests/test_client.py``) carries a raw ``str`` in ``.status`` — invisible
    everywhere status is only compared (``RecordStatus`` is itself a ``str``
    subclass), until ``allowed_commands`` -> ``decide`` -> a 409 message calls
    ``.value`` on it. Regression for ``RecordSnapshot.of`` normalising via
    ``RecordStatus(record.status)``.
    """
    rt = await lc.record_type("lc-ac-find-str-status")
    rec = Record(
        patient_id=lc.patient_id,
        study_uid=lc.study_uid,
        series_uid=lc.series_uid,
        record_type_name=rt,
        status="pending",  # raw str — see docstring
    )
    lc.session.add(rec)
    await lc.session.commit()

    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.post(RECORDS_FIND, json={})

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items and all("allowed_commands" in item for item in items)
