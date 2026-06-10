"""Unit tests for PhotoService (filesystem photo storage)."""

import pytest

from clarinet.services.photo_service import PhotoService
from clarinet.settings import settings


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    return PhotoService()


class TestValidateUpload:
    def test_allowed_type_passes_and_returns_type(self, service):
        assert service.validate_upload("image/png", 10) == "image/png"

    def test_disallowed_type_rejected(self, service):
        with pytest.raises(ValueError, match="not allowed"):
            service.validate_upload("text/html", 10)

    def test_none_type_rejected(self, service):
        with pytest.raises(ValueError, match="not allowed"):
            service.validate_upload(None, 10)

    def test_size_at_limit_passes(self, service):
        assert service.validate_upload("image/jpeg", service.max_upload_bytes()) == "image/jpeg"

    def test_size_over_limit_rejected(self, service):
        with pytest.raises(ValueError, match="too large"):
            service.validate_upload("image/jpeg", service.max_upload_bytes() + 1)


class TestSafeFilename:
    @pytest.mark.parametrize("bad", ["../x.jpg", "a/b.jpg", "", ".", ".."])
    def test_traversal_rejected(self, bad):
        with pytest.raises(FileNotFoundError):
            PhotoService._safe_filename(bad)

    def test_plain_name_accepted(self):
        assert PhotoService._safe_filename("abc.jpg") == "abc.jpg"


class TestExtensionFromContentType:
    @pytest.mark.asyncio
    async def test_extension_derived_from_content_type(self, service):
        info = await service.save_photo(1, b"png-bytes", "image/png")
        assert info.filename.endswith(".png")

    @pytest.mark.asyncio
    async def test_unknown_type_gets_no_executable_extension(self, service):
        # XSS regression: even if an unexpected type slips through validation,
        # the stored extension never comes from user input.
        info = await service.save_photo(1, b"data", "application/x-unknown")
        assert not info.filename.endswith((".html", ".svg"))


class TestRoundtrip:
    @pytest.mark.asyncio
    async def test_save_list_serve_delete(self, service):
        saved = await service.save_photo(7, b"abc", "image/jpeg")
        listed = await service.list_photos(7)
        assert [p.filename for p in listed] == [saved.filename]
        assert listed[0].size == 3
        path = await service.get_photo_path(7, saved.filename)
        assert path.read_bytes() == b"abc"
        await service.delete_photo(7, saved.filename)
        assert await service.list_photos(7) == []

    @pytest.mark.asyncio
    async def test_delete_missing_raises(self, service):
        with pytest.raises(FileNotFoundError):
            await service.delete_photo(7, "nope.jpg")

    @pytest.mark.asyncio
    async def test_delete_record_photos_removes_dir(self, service, tmp_path):
        await service.save_photo(9, b"a", "image/png")
        await service.save_photo(9, b"b", "image/webp")
        removed = await service.delete_record_photos(9)
        assert removed == 2
        assert not (tmp_path / "records" / "9").exists()

    @pytest.mark.asyncio
    async def test_delete_record_photos_missing_dir_ok(self, service):
        assert await service.delete_record_photos(404) == 0
