"""Service for managing record photos (immediate upload pattern)."""

from __future__ import annotations

import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from clarinet.settings import settings
from clarinet.utils.fs import run_in_fs_thread
from clarinet.utils.logger import logger

# Extensions derive from the validated content type, never from the
# client-supplied filename — a user-controlled extension would let
# .html/.svg uploads come back as executable content (stored XSS).
_EXTENSION_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/heic": ".heic",
    "image/webp": ".webp",
}


@dataclass
class PhotoInfo:
    """Metadata for an uploaded photo."""

    filename: str
    url: str
    size: int


class PhotoService:
    """Manages photo uploads, listing, serving and deletion for records.

    Photos are stored on the filesystem at
    ``{storage_path}/records/{record_id}/photos/``.
    No database interaction — purely filesystem-based.

    Deliberately outside ``FileRepository``: photos are UI artifacts keyed
    by the stable ``record_id``, so they survive anonymization and
    ``disk_path_template`` migrations that relocate DICOM working dirs.
    This class is the sole owner of the photo path layout — other code
    (e.g. cascade delete) must call into it instead of rebuilding paths.
    """

    @staticmethod
    def _photo_dir(record_id: int) -> Path:
        return Path(settings.storage_path) / "records" / str(record_id) / "photos"

    @staticmethod
    def _photo_url(record_id: int, filename: str) -> str:
        return f"/api/records/{record_id}/photos/{filename}"

    @staticmethod
    def _safe_filename(filename: str) -> str:
        """Validate filename to prevent path traversal.

        Raises:
            FileNotFoundError: If filename contains path separators or is empty.
        """
        safe = Path(filename).name
        if not safe or safe != filename or safe == "..":
            msg = f"Photo '{filename}' not found"
            raise FileNotFoundError(msg)
        return safe

    @staticmethod
    def max_upload_bytes() -> int:
        return settings.photos_max_size_mb * 1024 * 1024

    def validate_upload(self, content_type: str | None, size: int) -> str:
        """Validate file type and size.

        Returns:
            The validated, non-None content type.

        Raises:
            ValueError: If content type is not allowed or file exceeds size limit.
        """
        if content_type is None or content_type not in settings.photos_allowed_types:
            msg = (
                f"File type '{content_type}' is not allowed. "
                f"Allowed: {', '.join(settings.photos_allowed_types)}"
            )
            raise ValueError(msg)
        if size > self.max_upload_bytes():
            msg = f"File too large: exceeds {settings.photos_max_size_mb}MB limit"
            raise ValueError(msg)
        return content_type

    async def save_photo(self, record_id: int, content: bytes, content_type: str) -> PhotoInfo:
        """Save photo to disk and return metadata.

        The extension derives from the validated ``content_type``; the
        client-supplied filename is never trusted (stored-XSS vector).
        """
        ext = _EXTENSION_BY_TYPE.get(content_type) or mimetypes.guess_extension(content_type) or ""
        filename = f"{uuid4().hex}{ext}"
        photo_dir = self._photo_dir(record_id)

        await run_in_fs_thread(lambda: photo_dir.mkdir(parents=True, exist_ok=True))
        await run_in_fs_thread((photo_dir / filename).write_bytes, content)

        logger.info(f"Photo uploaded: record={record_id} file={filename} size={len(content)}")

        return PhotoInfo(
            filename=filename,
            url=self._photo_url(record_id, filename),
            size=len(content),
        )

    async def list_photos(self, record_id: int) -> list[PhotoInfo]:
        """List all photos for a record."""
        photo_dir = self._photo_dir(record_id)

        def _list() -> list[PhotoInfo]:
            if not photo_dir.exists():
                return []
            return [
                PhotoInfo(
                    filename=f.name,
                    url=self._photo_url(record_id, f.name),
                    size=f.stat().st_size,
                )
                for f in sorted(photo_dir.iterdir(), key=lambda p: p.stat().st_mtime)
                if f.is_file()
            ]

        return await run_in_fs_thread(_list)

    async def get_photo_path(self, record_id: int, filename: str) -> Path:
        """Return the filesystem path for a photo.

        Raises:
            FileNotFoundError: If filename is invalid or file doesn't exist.
        """
        safe = self._safe_filename(filename)
        path = self._photo_dir(record_id) / safe
        exists = await run_in_fs_thread(path.exists)
        if not exists:
            msg = f"Photo '{safe}' not found"
            raise FileNotFoundError(msg)
        return path

    @staticmethod
    def guess_media_type(path: Path) -> str:
        return mimetypes.guess_type(str(path))[0] or "application/octet-stream"

    async def delete_photo(self, record_id: int, filename: str) -> None:
        """Delete a photo from disk.

        Raises:
            FileNotFoundError: If filename is invalid or file doesn't exist.
        """
        safe = self._safe_filename(filename)
        path = self._photo_dir(record_id) / safe
        exists = await run_in_fs_thread(path.exists)
        if not exists:
            msg = f"Photo '{safe}' not found"
            raise FileNotFoundError(msg)
        await run_in_fs_thread(path.unlink)
        logger.info(f"Photo deleted: record={record_id} file={safe}")

    async def delete_record_photos(self, record_id: int) -> int:
        """Remove the record's entire photo directory (cascade-delete cleanup).

        Returns:
            Number of photo files removed; 0 if the directory doesn't exist.
        """
        photo_dir = self._photo_dir(record_id)

        def _purge() -> int:
            if not photo_dir.exists():
                return 0
            count = sum(1 for f in photo_dir.iterdir() if f.is_file())
            shutil.rmtree(photo_dir)
            record_dir = photo_dir.parent
            if record_dir.exists() and not any(record_dir.iterdir()):
                record_dir.rmdir()
            return count

        removed = await run_in_fs_thread(_purge)
        if removed:
            logger.info(f"Deleted {removed} photo(s) for record {record_id}")
        return removed
