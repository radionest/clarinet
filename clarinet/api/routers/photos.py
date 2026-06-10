"""Record photo upload endpoints — immediate upload pattern."""

from __future__ import annotations

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clarinet.api.dependencies import (
    AuthorizedRecordDep,
    CurrentUserDep,
    MutableRecordDep,
    PhotoServiceDep,
)
from clarinet.exceptions.http import BAD_REQUEST, NOT_FOUND

router = APIRouter()


class PhotoResponse(BaseModel):
    filename: str
    url: str
    size: int


def _deployment_url(request: Request, url: str) -> str:
    """Prefix a server-relative URL with the deployment sub-path (ASGI root_path)."""
    root_path: str = request.scope.get("root_path", "")
    return root_path.rstrip("/") + url


@router.post("/{record_id}/photos", status_code=201, response_model=PhotoResponse)
async def upload_photo(
    record: MutableRecordDep,
    file: UploadFile,
    service: PhotoServiceDep,
    user: CurrentUserDep,
    request: Request,
) -> PhotoResponse:
    """Upload a photo for a record.

    ``url`` is only valid for the current deployment (it includes the
    sub-path prefix). Clients that persist a reference must store
    ``filename`` and resolve the URL at display time.
    """
    assert record.id is not None
    # Read at most limit+1 bytes so an oversized body is rejected without
    # materializing it in memory.
    content = await file.read(service.max_upload_bytes() + 1)
    try:
        content_type = service.validate_upload(file.content_type, len(content))
    except ValueError as e:
        raise BAD_REQUEST.with_context(str(e)) from None
    result = await service.save_photo(record.id, content, content_type, user_id=user.id)
    return PhotoResponse(
        filename=result.filename,
        url=_deployment_url(request, result.url),
        size=result.size,
    )


@router.get("/{record_id}/photos", response_model=list[PhotoResponse])
async def list_photos(
    record: AuthorizedRecordDep,
    service: PhotoServiceDep,
    request: Request,
) -> list[PhotoResponse]:
    """List uploaded photos for a record."""
    assert record.id is not None
    results = await service.list_photos(record.id)
    return [
        PhotoResponse(filename=r.filename, url=_deployment_url(request, r.url), size=r.size)
        for r in results
    ]


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
    return FileResponse(
        path,
        media_type=service.guess_media_type(path),
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{record_id}/photos/{filename}", status_code=204)
async def delete_photo(
    record: MutableRecordDep,
    filename: str,
    service: PhotoServiceDep,
    user: CurrentUserDep,
) -> None:
    """Delete an uploaded photo."""
    assert record.id is not None
    try:
        await service.delete_photo(record.id, filename, user_id=user.id)
    except FileNotFoundError as e:
        raise NOT_FOUND.with_context(str(e)) from None
