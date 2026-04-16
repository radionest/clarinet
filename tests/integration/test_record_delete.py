"""Integration tests for DELETE /api/admin/records/{id} (cascade delete)."""

import pytest
import pytest_asyncio
from sqlmodel import select

from clarinet.models import Record
from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.file_schema import FileDefinition, FileRole, RecordFileLink, RecordTypeFileLink
from clarinet.settings import settings
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
    seed_record,
)
from tests.utils.urls import ADMIN_RECORDS


@pytest_asyncio.fixture
async def cascade_env(test_session, tmp_path, monkeypatch):
    """Seed a parent + two children with OUTPUT files on disk.

    Layout:
        root  ── child_a
              └─ child_b

    Every record has one OUTPUT file written to the shared series working folder.
    """
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))

    pat = make_patient("DEL_PAT", "Delete Patient")
    test_session.add(pat)
    await test_session.commit()
    await test_session.refresh(pat)

    study = make_study("DEL_PAT", "1.2.3.900")
    test_session.add(study)
    await test_session.commit()

    series = make_series("1.2.3.900", "1.2.3.900.1", 1)
    test_session.add(series)
    await test_session.commit()

    rt = make_record_type("del-rt", level=DicomQueryLevel.SERIES)
    test_session.add(rt)
    await test_session.commit()

    fd = FileDefinition(name="out", pattern="output_{id}.nrrd")
    test_session.add(fd)
    await test_session.commit()
    await test_session.refresh(fd)

    test_session.add(
        RecordTypeFileLink(
            record_type_name="del-rt",
            file_definition_id=fd.id,
            role=FileRole.OUTPUT,
            required=False,
        )
    )
    await test_session.commit()

    root = await seed_record(
        test_session,
        patient_id="DEL_PAT",
        study_uid="1.2.3.900",
        series_uid="1.2.3.900.1",
        rt_name="del-rt",
    )
    child_a = await seed_record(
        test_session,
        patient_id="DEL_PAT",
        study_uid="1.2.3.900",
        series_uid="1.2.3.900.1",
        rt_name="del-rt",
        parent_record_id=root.id,
    )
    child_b = await seed_record(
        test_session,
        patient_id="DEL_PAT",
        study_uid="1.2.3.900",
        series_uid="1.2.3.900.1",
        rt_name="del-rt",
        parent_record_id=root.id,
    )

    working_dir = tmp_path / pat.anon_id / "1.2.3.900" / "1.2.3.900.1"
    working_dir.mkdir(parents=True, exist_ok=True)
    files = {
        root.id: working_dir / f"output_{root.id}.nrrd",
        child_a.id: working_dir / f"output_{child_a.id}.nrrd",
        child_b.id: working_dir / f"output_{child_b.id}.nrrd",
    }
    for f in files.values():
        f.write_bytes(b"fake nrrd")

    return {
        "root": root,
        "child_a": child_a,
        "child_b": child_b,
        "files": files,
        "fd": fd,
    }


class TestDeleteRecordCascade:
    """Tests for DELETE /api/admin/records/{id}."""

    @pytest.mark.asyncio
    async def test_delete_cascade_success(self, client, cascade_env, test_session):
        root = cascade_env["root"]
        child_a = cascade_env["child_a"]
        child_b = cascade_env["child_b"]
        files = cascade_env["files"]

        resp = await client.delete(f"{ADMIN_RECORDS}/{root.id}")

        assert resp.status_code == 200
        data = resp.json()
        assert set(data["deleted_ids"]) == {root.id, child_a.id, child_b.id}
        assert data["files_removed"] == 3

        # Files gone from disk
        for f in files.values():
            assert not f.exists()

        # Records gone from DB
        for rid in (root.id, child_a.id, child_b.id):
            result = await test_session.execute(select(Record).where(Record.id == rid))
            assert result.scalars().first() is None

    @pytest.mark.asyncio
    async def test_delete_deep_tree(self, client, cascade_env, test_session, tmp_path):
        """Three-level tree (root → child → grandchild) must cascade fully.

        Regression: prior ORM `session.delete()` loop relied on implicit
        UoW ordering; a self-referential FK with only some levels eager-loaded
        could violate the constraint. Bulk SQL DELETE covers this path.
        """
        root = cascade_env["root"]
        child_a = cascade_env["child_a"]

        grandchild = await seed_record(
            test_session,
            patient_id="DEL_PAT",
            study_uid="1.2.3.900",
            series_uid="1.2.3.900.1",
            rt_name="del-rt",
            parent_record_id=child_a.id,
        )
        working_dir = cascade_env["files"][root.id].parent
        grand_file = working_dir / f"output_{grandchild.id}.nrrd"
        grand_file.write_bytes(b"grand data")

        resp = await client.delete(f"{ADMIN_RECORDS}/{root.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert grandchild.id in data["deleted_ids"]
        assert not grand_file.exists()

    @pytest.mark.asyncio
    async def test_delete_leaf_keeps_siblings(self, client, cascade_env, test_session):
        """Deleting a single child must not touch the root or its sibling."""
        root = cascade_env["root"]
        child_a = cascade_env["child_a"]
        child_b = cascade_env["child_b"]
        files = cascade_env["files"]

        resp = await client.delete(f"{ADMIN_RECORDS}/{child_a.id}")
        assert resp.status_code == 200
        assert resp.json()["deleted_ids"] == [child_a.id]

        assert not files[child_a.id].exists()
        assert files[root.id].exists()
        assert files[child_b.id].exists()

        for rid in (root.id, child_b.id):
            result = await test_session.execute(select(Record).where(Record.id == rid))
            assert result.scalars().first() is not None

    @pytest.mark.asyncio
    async def test_delete_inwork_blocks_whole_subtree(self, client, cascade_env, test_session):
        """If a single descendant is inwork, nothing is deleted."""
        root = cascade_env["root"]
        child_a = cascade_env["child_a"]
        files = cascade_env["files"]

        # Put one child into inwork state
        child_a_db = await test_session.get(Record, child_a.id)
        child_a_db.status = RecordStatus.inwork
        await test_session.commit()

        resp = await client.delete(f"{ADMIN_RECORDS}/{root.id}")
        assert resp.status_code == 409

        # Nothing deleted: all files still exist, all records still in DB
        for f in files.values():
            assert f.exists()
        for rid in files:
            result = await test_session.execute(select(Record).where(Record.id == rid))
            assert result.scalars().first() is not None

    @pytest.mark.asyncio
    async def test_delete_root_itself_inwork_blocks(self, client, cascade_env, test_session):
        root = cascade_env["root"]

        root_db = await test_session.get(Record, root.id)
        root_db.status = RecordStatus.inwork
        await test_session.commit()

        resp = await client.delete(f"{ADMIN_RECORDS}/{root.id}")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_delete_nonexistent_record(self, client):
        resp = await client.delete(f"{ADMIN_RECORDS}/999999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_deletes_file_links(self, client, cascade_env, test_session):
        """RecordFileLink rows for the subtree are cleaned up by ORM cascade."""
        root = cascade_env["root"]
        child_a = cascade_env["child_a"]
        fd = cascade_env["fd"]

        for rid in (root.id, child_a.id):
            test_session.add(
                RecordFileLink(
                    record_id=rid, file_definition_id=fd.id, filename=f"output_{rid}.nrrd"
                )
            )
        await test_session.commit()

        resp = await client.delete(f"{ADMIN_RECORDS}/{root.id}")
        assert resp.status_code == 200

        result = await test_session.execute(select(RecordFileLink))
        assert result.scalars().all() == []

    @pytest.mark.asyncio
    async def test_delete_survives_missing_output_file(self, client, cascade_env):
        """Missing OUTPUT files on disk must not break the cascade."""
        root = cascade_env["root"]
        files = cascade_env["files"]

        # Remove one OUTPUT file before calling DELETE
        files[root.id].unlink()

        resp = await client.delete(f"{ADMIN_RECORDS}/{root.id}")
        assert resp.status_code == 200
        for f in files.values():
            assert not f.exists()
