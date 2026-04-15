"""Record photo upload endpoints — immediate upload pattern."""

from __future__ import annotations

from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clarinet.api.dependencies import AuthorizedRecordDep, MutableRecordDep, PhotoServiceDep
from clarinet.exceptions.http import BAD_REQUEST, NOT_FOUND

router = APIRouter()


class PhotoResponse(BaseModel):
    filename: str
    url: str
    size: int


@router.post("/{record_id}/photos", status_code=201, response_model=PhotoResponse)
async def upload_photo(
    record: MutableRecordDep,
    file: UploadFile,
    service: PhotoServiceDep,
) -> PhotoResponse:
    """Upload a photo for a record."""
    assert record.id is not None
    content = await file.read()
    try:
        service.validate_upload(file.content_type, len(content))
    except ValueError as e:
        raise BAD_REQUEST.with_context(str(e)) from None
    result = await service.save_photo(record.id, content, file.filename)
    return PhotoResponse(filename=result.filename, url=result.url, size=result.size)


@router.get("/{record_id}/photos", response_model=list[PhotoResponse])
async def list_photos(
    record: AuthorizedRecordDep,
    service: PhotoServiceDep,
) -> list[PhotoResponse]:
    """List uploaded photos for a record."""
    assert record.id is not None
    results = await service.list_photos(record.id)
    return [PhotoResponse(filename=r.filename, url=r.url, size=r.size) for r in results]


@router.get("/{record_id}/photos/{filename}")
async def serve_photo(
    record: AuthorizedRecordDep,
    filename: str,
    service: PhotoServiceDep,
) -> FileResponse:
    """Serve an uploaded photo file."""
    assert record.id is not None
    try:
        path = await service.get_photo_path(record.id, filename)
    except FileNotFoundError as e:
        raise NOT_FOUND.with_context(str(e)) from None
    return FileResponse(path, media_type=service.guess_media_type(path))


@router.delete("/{record_id}/photos/{filename}", status_code=204)
async def delete_photo(
    record: MutableRecordDep,
    filename: str,
    service: PhotoServiceDep,
) -> None:
    """Delete an uploaded photo."""
    assert record.id is not None
    try:
        await service.delete_photo(record.id, filename)
    except FileNotFoundError as e:
        raise NOT_FOUND.with_context(str(e)) from None
