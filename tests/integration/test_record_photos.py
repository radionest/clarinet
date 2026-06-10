"""Integration tests for /api/records/{id}/photos endpoints."""

import pytest
import pytest_asyncio

from clarinet.models.base import DicomQueryLevel
from clarinet.settings import settings
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
    seed_record,
)
from tests.utils.urls import ADMIN_RECORDS, RECORDS_BASE

PNG_BYTES = b"\x89PNG fake"


def _upload_files(name="a.png", content=PNG_BYTES, ctype="image/png"):
    return {"file": (name, content, ctype)}


@pytest_asyncio.fixture
async def photo_env(test_session, tmp_path, monkeypatch):
    """A record to attach photos to, with storage redirected to tmp."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))

    pat = make_patient("PHOTO_PAT", "Photo Patient")
    test_session.add(pat)
    await test_session.commit()

    study = make_study("PHOTO_PAT", "1.2.3.800")
    test_session.add(study)
    await test_session.commit()

    series = make_series("1.2.3.800", "1.2.3.800.1", 1)
    test_session.add(series)
    await test_session.commit()

    rt = make_record_type("photo-rt", level=DicomQueryLevel.SERIES)
    test_session.add(rt)
    await test_session.commit()

    record = await seed_record(
        test_session,
        patient_id="PHOTO_PAT",
        study_uid="1.2.3.800",
        series_uid="1.2.3.800.1",
        rt_name="photo-rt",
    )
    return {"record": record, "tmp": tmp_path}


class TestPhotoEndpoints:
    @pytest.mark.asyncio
    async def test_upload_list_serve_delete_roundtrip(self, client, photo_env):
        rid = photo_env["record"].id

        resp = await client.post(f"{RECORDS_BASE}/{rid}/photos", files=_upload_files())
        assert resp.status_code == 201
        body = resp.json()
        assert body["filename"].endswith(".png")
        assert body["url"] == f"{RECORDS_BASE}/{rid}/photos/{body['filename']}"
        assert body["size"] == len(PNG_BYTES)

        resp = await client.get(f"{RECORDS_BASE}/{rid}/photos")
        assert resp.status_code == 200
        assert [p["filename"] for p in resp.json()] == [body["filename"]]

        resp = await client.get(f"{RECORDS_BASE}/{rid}/photos/{body['filename']}")
        assert resp.status_code == 200
        assert resp.content == PNG_BYTES
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.headers["x-content-type-options"] == "nosniff"

        resp = await client.delete(f"{RECORDS_BASE}/{rid}/photos/{body['filename']}")
        assert resp.status_code == 204

        resp = await client.get(f"{RECORDS_BASE}/{rid}/photos")
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_upload_rejects_disallowed_type(self, client, photo_env):
        rid = photo_env["record"].id
        resp = await client.post(
            f"{RECORDS_BASE}/{rid}/photos",
            files=_upload_files(name="evil.html", content=b"<script>", ctype="text/html"),
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_rejects_oversize(self, client, photo_env, monkeypatch):
        monkeypatch.setattr(settings, "photos_max_size_mb", 1)
        rid = photo_env["record"].id
        big = b"x" * (1024 * 1024 + 1)
        resp = await client.post(f"{RECORDS_BASE}/{rid}/photos", files=_upload_files(content=big))
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_extension_ignores_client_filename(self, client, photo_env):
        # Stored-XSS regression: an .html upload declared as image/png must not
        # come back with an executable extension or content type.
        rid = photo_env["record"].id
        resp = await client.post(
            f"{RECORDS_BASE}/{rid}/photos",
            files=_upload_files(name="evil.html", content=b"<script>alert(1)</script>"),
        )
        assert resp.status_code == 201
        filename = resp.json()["filename"]
        assert filename.endswith(".png")

        resp = await client.get(f"{RECORDS_BASE}/{rid}/photos/{filename}")
        assert resp.headers["content-type"].startswith("image/png")

    @pytest.mark.asyncio
    async def test_serve_missing_photo_404(self, client, photo_env):
        rid = photo_env["record"].id
        resp = await client.get(f"{RECORDS_BASE}/{rid}/photos/nope.png")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_missing_photo_404(self, client, photo_env):
        rid = photo_env["record"].id
        resp = await client.delete(f"{RECORDS_BASE}/{rid}/photos/nope.png")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cascade_delete_removes_photos(self, client, photo_env):
        rid = photo_env["record"].id
        resp = await client.post(f"{RECORDS_BASE}/{rid}/photos", files=_upload_files())
        assert resp.status_code == 201
        photo_dir = photo_env["tmp"] / "records" / str(rid) / "photos"
        assert photo_dir.exists()

        resp = await client.delete(f"{ADMIN_RECORDS}/{rid}")
        assert resp.status_code == 200
        assert resp.json()["files_removed"] == 1
        assert not photo_dir.exists()
