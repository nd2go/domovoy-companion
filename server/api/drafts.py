"""Owner-scoped draft and original-photo API."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from homebox_companion import HomeboxGateway

from ..dependencies import get_verified_gateway
from ..services import draft_store
from .inventory_schema import homebox

router = APIRouter(prefix="/drafts")
IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic", "image/heif"}


def _matches_image_type(mime: str, prefix: bytes) -> bool:
    if mime in {"image/jpeg", "image/jpg"}:
        return prefix.startswith(b"\xff\xd8\xff")
    if mime == "image/png":
        return prefix.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/webp":
        return prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP"
    return (
        len(prefix) >= 12
        and prefix[4:8] == b"ftyp"
        and prefix[8:12]
        in {
            b"heic",
            b"heix",
            b"hevc",
            b"hevx",
            b"mif1",
            b"msf1",
        }
    )


class NewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID


class DraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    state: dict


@dataclass(frozen=True)
class DraftScope:
    key: str
    owner: str
    environment: str
    instance: str
    collection: str


async def verified_scope(
    request: Request, gateway: Annotated[HomeboxGateway, Depends(get_verified_gateway)]
) -> DraftScope:
    group = await homebox(gateway, "/groups")
    group_id = gateway.access.group_id or (group.get("id") if isinstance(group, dict) else None)
    if not isinstance(group, dict) or not group_id or group.get("id") != group_id:
        raise HTTPException(403, "Homebox collection does not match the request")
    app_settings = request.app.state.settings
    instance = app_settings.api_url.rstrip("/")
    environment = app_settings.vision_environment
    owner = gateway.access.identity_scope
    key = hashlib.sha256(f"{environment}\n{instance}\n{group_id}\n{owner}".encode()).hexdigest()
    return DraftScope(key, owner, environment, instance, group_id)


Scope = Annotated[DraftScope, Depends(verified_scope)]


def _not_found(error: KeyError) -> HTTPException:
    return HTTPException(404, "Draft or photo not found")


@router.post("", status_code=201)
async def create_draft(body: NewDraft, scope: Scope) -> dict:
    try:
        return await asyncio.to_thread(
            draft_store.create,
            str(body.id),
            scope.key,
            scope.owner,
            scope.environment,
            scope.instance,
            scope.collection,
        )
    except KeyError as error:
        raise HTTPException(409, "Draft ID belongs to another scope") from error


@router.get("")
async def list_drafts(scope: Scope) -> list[dict]:
    return await asyncio.to_thread(draft_store.list_drafts, scope.key)


@router.get("/{draft_id}")
async def read_draft(draft_id: UUID, scope: Scope) -> dict:
    try:
        return await asyncio.to_thread(draft_store.read, str(draft_id), scope.key)
    except KeyError as error:
        raise _not_found(error) from error


@router.patch("/{draft_id}")
async def update_draft(draft_id: UUID, body: DraftUpdate, scope: Scope) -> dict:
    if body.state.get("id") != str(draft_id):
        raise HTTPException(422, "Draft state ID does not match the URL")
    try:
        return await asyncio.to_thread(draft_store.update, str(draft_id), scope.key, body.revision, body.state)
    except KeyError as error:
        raise _not_found(error) from error
    except FileExistsError as error:
        raise HTTPException(409, "Draft revision changed") from error
    except ValueError as error:
        raise HTTPException(413, str(error)) from error


@router.put("/{draft_id}/photos/{photo_id}")
async def upload_original(
    draft_id: UUID,
    photo_id: UUID,
    file: Annotated[UploadFile, File()],
    scope: Scope,
    request: Request,
) -> dict:
    app_settings = request.app.state.settings
    mime = file.content_type or ""
    if mime not in IMAGE_TYPES:
        raise HTTPException(415, "Unsupported image type")
    max_size = app_settings.max_upload_size_bytes
    if file.size is not None and file.size > max_size:
        raise HTTPException(413, "Photo exceeds upload limit")
    filename = Path(file.filename or "photo").name[:255]
    draft_store.PHOTOS.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".upload-", dir=draft_store.PHOTOS)
    temporary = Path(name)
    size = 0
    digest = hashlib.sha256()
    prefix = bytearray()
    try:
        with os.fdopen(fd, "wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(413, "Photo exceeds upload limit")
                free = shutil.disk_usage(draft_store.PHOTOS).free
                if free < len(chunk) + 1024 * 1024 * 1024:
                    raise HTTPException(507, "Not enough space to retain the original photo")
                target.write(chunk)
                digest.update(chunk)
                prefix.extend(chunk[: max(0, 32 - len(prefix))])
            if size == 0:
                raise HTTPException(400, "Empty photo")
            if not _matches_image_type(mime, bytes(prefix)):
                raise HTTPException(415, "Photo bytes do not match image type")
            target.flush()
            os.fsync(target.fileno())
        try:
            return await asyncio.to_thread(
                draft_store.add_photo,
                str(draft_id),
                scope.key,
                str(photo_id),
                temporary,
                filename,
                mime,
                size,
                digest.hexdigest(),
                app_settings.capture_max_images,
            )
        except KeyError as error:
            raise _not_found(error) from error
        except FileExistsError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(413, str(error)) from error
    except OSError as error:
        raise HTTPException(507, "Could not retain the original photo") from error
    finally:
        temporary.unlink(missing_ok=True)


@router.get("/{draft_id}/photos/{photo_id}/original")
async def download_original(draft_id: UUID, photo_id: UUID, scope: Scope) -> FileResponse:
    try:
        path, photo = await asyncio.to_thread(draft_store.photo_path, str(draft_id), scope.key, str(photo_id))
    except KeyError as error:
        raise _not_found(error) from error
    if not path.is_file():
        raise HTTPException(503, "Accepted original photo is unavailable")
    return FileResponse(
        path,
        filename=photo["filename"],
        media_type="application/octet-stream",
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.put("/{draft_id}/derived/{checksum}")
async def upload_derived(
    draft_id: UUID,
    checksum: str,
    file: Annotated[UploadFile, File()],
    scope: Scope,
    request: Request,
) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise HTTPException(422, "Invalid checksum")
    mime = file.content_type or ""
    if mime not in IMAGE_TYPES:
        raise HTTPException(415, "Unsupported derived image type")
    maximum = request.app.state.settings.max_upload_size_bytes
    if file.size is not None and file.size > maximum:
        raise HTTPException(413, "Derived image exceeds upload limit")
    draft_store.DERIVED.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".upload-", dir=draft_store.DERIVED)
    temporary = Path(name)
    size = 0
    digest = hashlib.sha256()
    prefix = bytearray()
    try:
        with os.fdopen(fd, "wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(413, "Derived image exceeds upload limit")
                if shutil.disk_usage(draft_store.DERIVED).free < len(chunk) + 1024 * 1024 * 1024:
                    raise HTTPException(507, "Not enough space to retain the derived image")
                target.write(chunk)
                digest.update(chunk)
                prefix.extend(chunk[: max(0, 32 - len(prefix))])
            if size == 0 or digest.hexdigest() != checksum:
                raise HTTPException(409, "Derived image checksum does not match")
            if not _matches_image_type(mime, bytes(prefix)):
                raise HTTPException(415, "Derived bytes do not match image type")
            target.flush()
            os.fsync(target.fileno())
        try:
            return await asyncio.to_thread(
                draft_store.add_derived,
                str(draft_id),
                scope.key,
                temporary,
                mime,
                size,
                checksum,
            )
        except KeyError as error:
            raise _not_found(error) from error
    except OSError as error:
        raise HTTPException(507, "Could not retain the derived image") from error
    finally:
        temporary.unlink(missing_ok=True)


@router.get("/{draft_id}/derived/{checksum}")
async def download_derived(draft_id: UUID, checksum: str, scope: Scope) -> FileResponse:
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise HTTPException(422, "Invalid checksum")
    try:
        path, derived = await asyncio.to_thread(draft_store.derived_path, str(draft_id), scope.key, checksum)
    except KeyError as error:
        raise _not_found(error) from error
    if not path.is_file():
        raise HTTPException(503, "Accepted derived image is unavailable")
    return FileResponse(path, media_type=derived["mime"], headers={"X-Content-Type-Options": "nosniff"})


@router.delete("/{draft_id}", status_code=204)
async def delete_draft(draft_id: UUID, scope: Scope) -> None:
    try:
        await asyncio.to_thread(draft_store.delete, str(draft_id), scope.key)
    except KeyError as error:
        raise _not_found(error) from error
