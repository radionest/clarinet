"""Integration tests for GET /api/records/{id}/output-files/{file_name}."""

import pytest
import pytest_asyncio

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
from tests.utils.urls import RECORDS_BASE


@pytest_asyncio.fixture
async def output_download_env(test_session, tmp_path, monkeypatch):
    """Seed a record with an OUTPUT FileDefinition + a real file on disk and a RecordFileLink row."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))

    pat = make_patient("DLPAT", "Download Patient")
    test_session.add(pat)
    await test_session.commit()

    study = make_study("DLPAT", "1.2.3.900")
    test_session.add(study)
    await test_session.commit()

    series = make_series("1.2.3.900", "1.2.3.900.1", 1)
    test_session.add(series)
    await test_session.commit()

    rt = make_record_type("dl-test-rt", level=DicomQueryLevel.SERIES)
    test_session.add(rt)
    await test_session.commit()

    fd = FileDefinition(name="primary_output", pattern="result_{id}.json")
    test_session.add(fd)
    await test_session.commit()
    await test_session.refresh(fd)

    test_session.add(
        RecordTypeFileLink(
            record_type_name="dl-test-rt",
            file_definition_id=fd.id,
            role=FileRole.OUTPUT,
            required=False,
        )
    )
    await test_session.commit()

    rec = await seed_record(
        test_session,
        patient_id="DLPAT",
        study_uid="1.2.3.900",
        series_uid="1.2.3.900.1",
        rt_name="dl-test-rt",
    )

    await test_session.refresh(pat)
    working_dir = tmp_path / pat.anon_id / "1.2.3.900" / "1.2.3.900.1"
    working_dir.mkdir(parents=True, exist_ok=True)
    output_file = working_dir / f"result_{rec.id}.json"
    payload = b'{"result": "ok"}'
    output_file.write_bytes(payload)

    test_session.add(
        RecordFileLink(
            record_id=rec.id,
            file_definition_id=fd.id,
            filename=output_file.name,
            checksum="deadbeef",
        )
    )
    await test_session.commit()

    return {"record": rec, "output_file": output_file, "fd": fd, "payload": payload}


class TestDownloadOutputFile:
    """Tests for GET /api/records/{id}/output-files/{file_name}."""

    @pytest.mark.asyncio
    async def test_download_output_file_success(self, client, output_download_env):
        rec = output_download_env["record"]
        payload = output_download_env["payload"]
        output_file = output_download_env["output_file"]

        resp = await client.get(f"{RECORDS_BASE}/{rec.id}/output-files/primary_output")

        assert resp.status_code == 200
        assert resp.content == payload
        cd = resp.headers["content-disposition"]
        assert "attachment" in cd
        assert output_file.name in cd
        assert resp.headers["content-type"].startswith("application/json")

    @pytest.mark.asyncio
    async def test_download_unknown_definition_404(self, client, output_download_env):
        rec = output_download_env["record"]

        resp = await client.get(f"{RECORDS_BASE}/{rec.id}/output-files/does_not_exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_download_file_not_on_disk_404(self, client, output_download_env):
        rec = output_download_env["record"]
        output_download_env["output_file"].unlink()

        resp = await client.get(f"{RECORDS_BASE}/{rec.id}/output-files/primary_output")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_download_invalid_file_name_format(self, client, output_download_env):
        rec = output_download_env["record"]

        # Path component with a slash would change the route shape — FastAPI
        # treats it as a different path. Test the explicit pattern guard:
        # leading digit / hyphen are rejected by the path-param pattern.
        resp = await client.get(f"{RECORDS_BASE}/{rec.id}/output-files/9bad")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_download_nonexistent_record_404(self, client):
        resp = await client.get(f"{RECORDS_BASE}/999999/output-files/primary_output")
        assert resp.status_code == 404


class TestDownloadOutputFileMultiple:
    """Tests for ``multiple=True`` glob expansion path in resolve_output_file."""

    @pytest_asyncio.fixture
    async def multi_output_env(self, test_session, tmp_path, monkeypatch):
        """Seed a record_type with one ``multiple=True`` OUTPUT FileDefinition + 2 files on disk."""
        monkeypatch.setattr(settings, "storage_path", str(tmp_path))

        pat = make_patient("MULTPAT", "Multi Patient")
        test_session.add(pat)
        await test_session.commit()

        study = make_study("MULTPAT", "1.2.3.901")
        test_session.add(study)
        await test_session.commit()

        series = make_series("1.2.3.901", "1.2.3.901.1", 1)
        test_session.add(series)
        await test_session.commit()

        rt = make_record_type("multi-test-rt", level=DicomQueryLevel.SERIES)
        test_session.add(rt)
        await test_session.commit()

        fd = FileDefinition(name="segments", pattern="seg_*.nrrd", multiple=True)
        test_session.add(fd)
        await test_session.commit()
        await test_session.refresh(fd)

        test_session.add(
            RecordTypeFileLink(
                record_type_name="multi-test-rt",
                file_definition_id=fd.id,
                role=FileRole.OUTPUT,
                required=False,
            )
        )
        await test_session.commit()

        rec = await seed_record(
            test_session,
            patient_id="MULTPAT",
            study_uid="1.2.3.901",
            series_uid="1.2.3.901.1",
            rt_name="multi-test-rt",
        )

        await test_session.refresh(pat)
        working_dir = tmp_path / pat.anon_id / "1.2.3.901" / "1.2.3.901.1"
        working_dir.mkdir(parents=True, exist_ok=True)
        first = working_dir / "seg_a.nrrd"
        second = working_dir / "seg_b.nrrd"
        first.write_bytes(b"first segment")
        second.write_bytes(b"second segment")

        return {"record": rec, "files": [first, second]}

    @pytest.mark.asyncio
    async def test_download_multiple_glob_returns_first_lex(self, client, multi_output_env):
        """``multiple=True``: endpoint deterministically serves the lex-first match."""
        rec = multi_output_env["record"]
        first = sorted(multi_output_env["files"])[0]

        resp = await client.get(f"{RECORDS_BASE}/{rec.id}/output-files/segments")
        assert resp.status_code == 200
        assert resp.content == first.read_bytes()
        cd = resp.headers["content-disposition"]
        assert "attachment" in cd
        assert first.name in cd

    @pytest.mark.asyncio
    async def test_download_multiple_no_matches_404(self, client, multi_output_env):
        rec = multi_output_env["record"]
        for f in multi_output_env["files"]:
            f.unlink()

        resp = await client.get(f"{RECORDS_BASE}/{rec.id}/output-files/segments")
        assert resp.status_code == 404
