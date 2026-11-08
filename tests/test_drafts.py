"""Durable draft API contracts with synthetic photos."""

from __future__ import annotations

import base64
import errno
import hashlib
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from homebox_companion.core.config import Settings
from server.api.drafts import DraftScope, verified_scope
from server.app import create_app
from server.services import draft_store


@pytest.mark.asyncio
async def test_configured_instance_environment_and_verified_owner_define_scope(monkeypatch):
    group = str(uuid4())
    settings = SimpleNamespace(
        api_url="http://homebox/api/v1", vision_homebox_instance=str(uuid4()), vision_environment="dev"
    )
    request = MagicMock()
    request.app.state.settings = settings
    gateway = MagicMock()
    gateway.access = SimpleNamespace(group_id=group, identity_scope="owner")
    monkeypatch.setattr("server.api.drafts.homebox", AsyncMock(return_value={"id": group}))
    initial = await verified_scope(request, gateway)
    settings.vision_homebox_instance = str(uuid4())
    assert (await verified_scope(request, gateway)).key != initial.key
    settings.vision_homebox_instance = initial.instance
    settings.vision_environment = "qa"
    assert (await verified_scope(request, gateway)).key != initial.key
    settings.vision_environment = "dev"
    gateway.access.identity_scope = "peer"
    assert (await verified_scope(request, gateway)).key != initial.key


@pytest.mark.asyncio
async def test_draft_original_restart_idempotency_and_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(draft_store, "ROOT", tmp_path / "drafts")
    monkeypatch.setattr(draft_store, "DB", draft_store.ROOT / "drafts.sqlite3")
    monkeypatch.setattr(draft_store, "PHOTOS", draft_store.ROOT / "originals")
    monkeypatch.setattr(draft_store, "DERIVED", draft_store.ROOT / "derived")
    current = [DraftScope("scope-a", "owner-a", "dev", "instance-a", "collection-a")]
    settings = Settings(_env_file=None, homebox_api_key=SecretStr("hb_test"), disable_update_check=True)

    def app():
        result = create_app(settings)
        result.dependency_overrides[verified_scope] = lambda: current[0]
        return result

    draft_id, photo_id = str(uuid4()), str(uuid4())
    headers = {"X-Companion-Request": "1"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app()), base_url="http://test", headers=headers
    ) as client:
        created = await client.post("/api/drafts", json={"id": draft_id})
        assert created.status_code == 201, created.text
        assert created.json()["revision"] == 0
        assert (await client.post("/api/drafts", json={"id": draft_id})).status_code == 201
        url = f"/api/drafts/{draft_id}/photos/{photo_id}"
        content = b"\xff\xd8\xff synthetic image bytes"
        files = {"file": ("control.jpg", content, "image/jpeg")}
        first = await client.put(url, files=files)
        assert first.status_code == 200, first.text
        assert (await client.put(url, files=files)).json()["checksum"] == first.json()["checksum"]
        conflict = await client.put(url, files={"file": ("control.jpg", content + b"x", "image/jpeg")})
        assert conflict.status_code == 409
        state = {"id": draft_id, "status": "capturing", "locationId": "box-1", "images": [{"id": photo_id}]}
        saved = await client.patch(f"/api/drafts/{draft_id}", json={"revision": 0, "state": state})
        assert saved.status_code == 200, saved.text
        assert (
            await client.patch(f"/api/drafts/{draft_id}", json={"revision": 1, "state": {"id": str(uuid4())}})
        ).status_code == 422
        assert saved.json()["revision"] == 1
        with draft_store.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
        assert (await client.patch(f"/api/drafts/{draft_id}", json={"revision": 0, "state": state})).status_code == 409
        state["submission"] = {"itemStatuses": {"0": "failed"}, "createdItemIds": {"0": "item-1"}}
        saved = await client.patch(f"/api/drafts/{draft_id}", json={"revision": 1, "state": state})
        assert saved.status_code == 200
        with draft_store.connection() as db:
            assert db.execute("SELECT revision FROM operations WHERE id='submission'").fetchone()[0] == 2
        derivative = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        checksum = hashlib.sha256(derivative).hexdigest()
        derived_url = f"/api/drafts/{draft_id}/derived/{checksum}"
        assert (
            await client.put(derived_url, files={"file": ("thumb.png", derivative, "image/png")})
        ).status_code == 200
        assert (await client.get(derived_url)).content == derivative
        assert (
            await client.put(derived_url, files={"file": ("thumb.png", derivative, "image/png")})
        ).status_code == 200
        assert (
            await client.put(
                f"/api/drafts/{draft_id}/derived/{'0' * 64}", files={"file": ("thumb.png", derivative, "image/png")}
            )
        ).status_code == 409
        original = await client.get(f"{url}/original")
        assert original.content == content
        assert original.headers["content-disposition"].startswith("attachment;")
        assert len((await client.get("/api/drafts")).json()) == 1

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app()), base_url="http://test", headers=headers
    ) as client:
        assert (await client.get(f"/api/drafts/{draft_id}")).json()["state"] == state
        current[0] = DraftScope("scope-b", "owner-b", "dev", "instance-a", "collection-a")
        assert (await client.get(f"/api/drafts/{draft_id}")).status_code == 404
        assert (await client.post("/api/drafts", json={"id": draft_id})).status_code == 409
        current[0] = DraftScope("scope-c", "owner-a", "dev", "instance-a", "collection-b")
        assert (await client.get(f"/api/drafts/{draft_id}")).status_code == 404
        assert (await client.get(f"{url}/original")).status_code == 404
        current[0] = DraftScope("scope-a", "owner-a", "dev", "instance-a", "collection-a")
        assert (await client.delete(f"/api/drafts/{draft_id}")).status_code == 204
        assert (await client.get(f"/api/drafts/{draft_id}")).status_code == 404
        assert list(draft_store.PHOTOS.iterdir()) == []
        assert list(draft_store.DERIVED.iterdir()) == []


@pytest.mark.asyncio
async def test_draft_rejects_unsafe_uploads_and_low_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(draft_store, "ROOT", tmp_path / "drafts")
    monkeypatch.setattr(draft_store, "DB", draft_store.ROOT / "drafts.sqlite3")
    monkeypatch.setattr(draft_store, "PHOTOS", draft_store.ROOT / "originals")
    monkeypatch.setattr(draft_store, "DERIVED", draft_store.ROOT / "derived")
    app = create_app(Settings(_env_file=None, homebox_api_key=SecretStr("hb_test"), disable_update_check=True))
    app.dependency_overrides[verified_scope] = lambda: DraftScope("scope", "owner", "dev", "instance", "collection")
    headers = {"X-Companion-Request": "1"}
    draft_id, photo_id = str(uuid4()), str(uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", headers=headers) as client:
        assert (await client.post("/api/drafts", json={"id": draft_id})).status_code == 201
        url = f"/api/drafts/{draft_id}/photos/{photo_id}"
        assert (await client.put(url, files={"file": ("bad.svg", b"<svg/>", "image/svg+xml")})).status_code == 415
        assert (await client.put(url, files={"file": ("fake.jpg", b"not an image", "image/jpeg")})).status_code == 415
        assert (await client.put(url, files={"file": ("empty.jpg", b"", "image/jpeg")})).status_code == 400
        monkeypatch.setattr("server.api.drafts.shutil.disk_usage", lambda _: SimpleNamespace(free=0))
        assert (await client.put(url, files={"file": ("full.jpg", b"image", "image/jpeg")})).status_code == 507
        assert (await client.get(f"/api/drafts/{draft_id}")).json()["photos"] == []


@pytest.mark.asyncio
async def test_full_disk_during_file_creation_or_database_commit_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(draft_store, "ROOT", tmp_path / "drafts")
    monkeypatch.setattr(draft_store, "DB", draft_store.ROOT / "drafts.sqlite3")
    monkeypatch.setattr(draft_store, "PHOTOS", draft_store.ROOT / "originals")
    monkeypatch.setattr(draft_store, "DERIVED", draft_store.ROOT / "derived")
    app = create_app(Settings(_env_file=None, homebox_api_key=SecretStr("hb_test"), disable_update_check=True))
    app.dependency_overrides[verified_scope] = lambda: DraftScope("scope", "owner", "dev", "instance", "collection")
    draft_id, photo_id = str(uuid4()), str(uuid4())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test", headers={"X-Companion-Request": "1"}
    ) as client:
        await client.post("/api/drafts", json={"id": draft_id})

        def no_space(*_args, **_kwargs):
            raise OSError(errno.ENOSPC, "No space left on device")

        with monkeypatch.context() as patch:
            patch.setattr("server.api.drafts.tempfile.mkstemp", no_space)
            response = await client.put(
                f"/api/drafts/{draft_id}/photos/{photo_id}",
                files={"file": ("item.jpg", b"\xff\xd8\xff image", "image/jpeg")},
            )
            assert response.status_code == 507
        assert (await client.get(f"/api/drafts/{draft_id}")).json()["photos"] == []

        def database_full(*_args):
            error = sqlite3.OperationalError("database or disk is full")
            error.sqlite_errorcode = sqlite3.SQLITE_FULL
            raise error

        with monkeypatch.context() as patch:
            patch.setattr(draft_store, "update", database_full)
            response = await client.patch(f"/api/drafts/{draft_id}", json={"revision": 0, "state": {"id": draft_id}})
            assert response.status_code == 507
        assert (await client.get(f"/api/drafts/{draft_id}")).json()["revision"] == 0


@pytest.mark.asyncio
async def test_interrupted_photo_commit_is_not_acknowledged(tmp_path, monkeypatch):
    monkeypatch.setattr(draft_store, "ROOT", tmp_path / "drafts")
    monkeypatch.setattr(draft_store, "DB", draft_store.ROOT / "drafts.sqlite3")
    monkeypatch.setattr(draft_store, "PHOTOS", draft_store.ROOT / "originals")
    monkeypatch.setattr(draft_store, "DERIVED", draft_store.ROOT / "derived")
    app = create_app(Settings(_env_file=None, homebox_api_key=SecretStr("hb_test"), disable_update_check=True))
    app.dependency_overrides[verified_scope] = lambda: DraftScope("scope", "owner", "dev", "instance", "collection")
    draft_id, photo_id = str(uuid4()), str(uuid4())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test", headers={"X-Companion-Request": "1"}
    ) as client:
        await client.post("/api/drafts", json={"id": draft_id})

        def fail_replace(*_args):
            raise OSError("interrupted")

        with monkeypatch.context() as patch:
            patch.setattr(draft_store.os, "replace", fail_replace)
            result = await client.put(
                f"/api/drafts/{draft_id}/photos/{photo_id}",
                files={"file": ("item.jpg", b"\xff\xd8\xff image", "image/jpeg")},
            )
        assert result.status_code == 507
        assert (await client.get(f"/api/drafts/{draft_id}")).json()["photos"] == []
        assert list(draft_store.PHOTOS.iterdir()) == []
