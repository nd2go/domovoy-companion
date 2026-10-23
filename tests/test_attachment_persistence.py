"""Check attachment persistence without a live server."""

import httpx
import pytest

from homebox_companion import HomeboxClient
from homebox_companion.core.exceptions import HomeboxAPIError


@pytest.mark.asyncio
@pytest.mark.parametrize("persisted", [True, False])
async def test_upload_checks_independent_item_read(persisted):
    item = {"attachments": []}
    calls = []

    def upstream(request):
        calls.append(request.method)
        if request.method == "POST" and persisted:
            item["attachments"].append({"id": "photo-1", "type": "photo", "thumbnail": {"id": "thumb-1"}})
        return httpx.Response(200, json=item)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as transport:
        async with HomeboxClient(base_url="http://homebox.test/api/v1", client=transport) as client:
            if persisted:
                result = await client.upload_attachment("session", "item-1", b"photo", "photo.jpg")
                assert result == item
            else:
                with pytest.raises(HomeboxAPIError, match="did not persist"):
                    await client.upload_attachment("session", "item-1", b"photo", "photo.jpg")
    assert calls == ["GET", "POST", "GET"]


@pytest.mark.asyncio
async def test_upload_waits_for_new_photo_transaction(monkeypatch):
    calls = []
    photo = {"id": "new", "type": "photo", "thumbnail": None}

    async def no_wait(_delay):
        pass

    monkeypatch.setattr("homebox_companion.homebox.client.asyncio.sleep", no_wait)

    def upstream(request):
        calls.append(request.method)
        if len(calls) == 1:
            return httpx.Response(200, json={"attachments": []})
        if len(calls) > 3:
            photo["thumbnail"] = {"id": "thumb"}
        return httpx.Response(200, json={"attachments": [photo]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as transport:
        async with HomeboxClient(base_url="http://homebox.test/api/v1", client=transport) as client:
            result = await client.upload_attachment("session", "item-1", b"photo", "photo.jpg")
    assert result["attachments"][0]["thumbnail"] == {"id": "thumb"}
    assert calls == ["GET", "POST", "GET", "GET"]


@pytest.mark.asyncio
async def test_disabled_thumbnails_do_not_fail_or_repeat_saved_upload(monkeypatch):
    calls = []
    photo = {"id": "new", "type": "photo", "thumbnail": None}

    async def no_wait(_delay):
        pass

    monkeypatch.setattr("homebox_companion.homebox.client.asyncio.sleep", no_wait)

    def upstream(request):
        calls.append(request.method)
        return httpx.Response(200, json={"attachments": [] if len(calls) == 1 else [photo]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as transport:
        async with HomeboxClient(base_url="http://homebox.test/api/v1", client=transport) as client:
            result = await client.upload_attachment("session", "item-1", b"photo", "photo.jpg")
    assert result["attachments"] == [photo]
    assert calls == ["GET", "POST", "GET"] + ["GET"] * 20


@pytest.mark.asyncio
async def test_uncertain_post_is_not_repeated():
    calls = []

    def upstream(request):
        calls.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("Lost response", request=request)
        return httpx.Response(200, json={"attachments": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as transport:
        async with HomeboxClient(base_url="http://homebox.test/api/v1", client=transport) as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.upload_attachment("session", "item-1", b"photo", "photo.jpg")
    assert calls == ["GET", "POST"]


@pytest.mark.asyncio
async def test_thumbnail_read_lock_does_not_repeat_saved_upload(monkeypatch):
    calls = []
    photo = {"id": "new", "type": "photo", "thumbnail": None}

    async def no_wait(_delay):
        pass

    monkeypatch.setattr("homebox_companion.homebox.client.asyncio.sleep", no_wait)

    def upstream(request):
        calls.append(request.method)
        if len(calls) == 1:
            return httpx.Response(200, json={"attachments": []})
        if len(calls) == 4:
            return httpx.Response(500, json={"error": "database is locked"})
        if len(calls) > 4:
            photo["thumbnail"] = {"id": "thumb"}
        return httpx.Response(200, json={"attachments": [photo]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as transport:
        async with HomeboxClient(base_url="http://homebox.test/api/v1", client=transport) as client:
            result = await client.upload_attachment("session", "item-1", b"photo", "photo.jpg")
    assert result["attachments"][0]["id"] == "new"
    assert calls == ["GET", "POST", "GET", "GET", "GET"]
