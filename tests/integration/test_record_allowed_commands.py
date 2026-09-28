"""RecordRead.allowed_commands: the viewer's commands, decided by the lifecycle policy."""

import pytest

from clarinet.models import RecordStatus as S
from tests.utils.lifecycle import client_as
from tests.utils.urls import ADMIN_RECORDS, RECORDS_BASE


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
