"""Integration tests for DELETE /api/admin/records/{id}/output-files."""

import pytest
import pytest_asyncio
from sqlmodel import select

from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileDefinition, FileRole, RecordFileLink, RecordTypeFileLink
from clarinet.settings import settings
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
    seed_record,
)
from tests.utils.urls import ADMIN_RECORD_OUTPUT_FILES


@pytest_asyncio.fixture
async def output_file_env(test_session, tmp_path, monkeypatch):
    """Seed entities + create a physical output file on disk."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))

    pat = make_patient("OUTF_PAT", "Output Patient")
    test_session.add(pat)
    await test_session.commit()

    study = make_study("OUTF_PAT", "1.2.3.800")
    test_session.add(study)
    await test_session.commit()

    series = make_series("1.2.3.800", "1.2.3.800.1", 1)
    test_session.add(series)
    await test_session.commit()

    rt = make_record_type("outf-test-rt", level=DicomQueryLevel.SERIES)
    test_session.add(rt)
    await test_session.commit()

    # Create a FileDefinition for output
    fd = FileDefinition(name="test_output", pattern="output_{id}.nrrd")
    test_session.add(fd)
    await test_session.commit()
    await test_session.refresh(fd)

    # Link file def to record type as OUTPUT
    rt_link = RecordTypeFileLink(
        record_type_name="outf-test-rt",
        file_definition_id=fd.id,
        role=FileRole.OUTPUT,
        required=False,
    )
    test_session.add(rt_link)
    await test_session.commit()

    rec = await seed_record(
        test_session,
        patient_id="OUTF_PAT",
        study_uid="1.2.3.800",
        series_uid="1.2.3.800.1",
        rt_name="outf-test-rt",
    )

    # working_folder uses patient.anon_id (CLARINET_{auto_id}), not patient_id
    await test_session.refresh(pat)
    working_dir = tmp_path / pat.anon_id / "1.2.3.800" / "1.2.3.800.1"
    working_dir.mkdir(parents=True, exist_ok=True)
    output_file = working_dir / f"output_{rec.id}.nrrd"
    output_file.write_bytes(b"fake nrrd data")

    return {"record": rec, "output_file": output_file, "fd": fd}


class TestClearOutputFiles:
    """Tests for DELETE /api/admin/records/{id}/output-files."""

    @pytest.mark.asyncio
    async def test_clear_output_files_success(self, client, output_file_env):
        rec = output_file_env["record"]
        output_file = output_file_env["output_file"]

        assert output_file.exists()

        resp = await client.delete(f"{ADMIN_RECORD_OUTPUT_FILES}/{rec.id}/output-files")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["deleted_files"]) == 1
        assert output_file.name in data["deleted_files"]
        assert not output_file.exists()

    @pytest.mark.asyncio
    async def test_clear_output_files_finished_record_rejected(self, client, output_file_env):
        rec = output_file_env["record"]

        # Set record to finished
        await client.patch(
            f"/api/admin/records/{rec.id}/status",
            params={"record_status": "finished"},
        )

        resp = await client.delete(f"{ADMIN_RECORD_OUTPUT_FILES}/{rec.id}/output-files")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_clear_output_files_no_file_on_disk(self, client, output_file_env):
        rec = output_file_env["record"]
        output_file = output_file_env["output_file"]

        output_file.unlink()

        resp = await client.delete(f"{ADMIN_RECORD_OUTPUT_FILES}/{rec.id}/output-files")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_files"] == []
        assert data["deleted_links"] == 0

    @pytest.mark.asyncio
    async def test_clear_output_files_allowed_for_inwork(self, client, output_file_env):
        rec = output_file_env["record"]
        output_file = output_file_env["output_file"]

        # Set to inwork
        await client.patch(
            f"/api/admin/records/{rec.id}/status",
            params={"record_status": "inwork"},
        )

        resp = await client.delete(f"{ADMIN_RECORD_OUTPUT_FILES}/{rec.id}/output-files")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["deleted_files"]) == 1
        assert not output_file.exists()

    @pytest.mark.asyncio
    async def test_clear_output_files_nonexistent_record(self, client):
        resp = await client.delete(f"{ADMIN_RECORD_OUTPUT_FILES}/999999/output-files")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_clear_output_files_deletes_record_file_links(
        self, client, output_file_env, test_session
    ):
        """If RecordFileLink rows exist for OUTPUT files, they should be deleted."""
        rec = output_file_env["record"]
        fd = output_file_env["fd"]

        # Manually create a RecordFileLink for the output file
        link = RecordFileLink(
            record_id=rec.id,
            file_definition_id=fd.id,
            filename=f"output_{rec.id}.nrrd",
            checksum="abc123",
        )
        test_session.add(link)
        await test_session.commit()

        resp = await client.delete(f"{ADMIN_RECORD_OUTPUT_FILES}/{rec.id}/output-files")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_links"] == 1

        # Verify link is gone
        result = await test_session.execute(
            select(RecordFileLink).where(RecordFileLink.record_id == rec.id)
        )
        assert result.scalars().first() is None
