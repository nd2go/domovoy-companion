"""Keep the selected container during item creation and updates."""

from unittest.mock import AsyncMock

import httpx
import pytest

from homebox_companion import HomeboxClient
from homebox_companion.core.config import Settings
from server.api import items as items_api
from server.app import create_app
from server.dependencies import get_gateway

pytestmark = pytest.mark.unit


@pytest.fixture
def gateway(monkeypatch):
    gateway = AsyncMock(spec=HomeboxClient)
    gateway.create_item.return_value = {"id": "item", "name": "Control"}
    gateway.get_item.return_value = {
        "id": "item",
        "name": "Control",
        "description": "Original description",
        "quantity": 2,
        "parent": {"id": "box"},
        "tags": [{"id": "tag"}],
        "serialNumber": "CONTROL-1",
        "manufacturer": "Control maker",
        "assetId": "000-001",
        "insured": True,
        "syncChildEntityLocations": True,
        "attachments": [{"id": "photo"}],
        "fields": [{"id": "field", "name": "Source", "type": "text", "textValue": "Label"}],
    }
    gateway.update_item.return_value = {"id": "item", "name": "Control"}
    gateway.ensure_asset_ids.return_value = 0
    monkeypatch.setattr(items_api, "get_valid_tag_ids", AsyncMock(return_value={"tag"}))
    return gateway


async def send(gateway, method, path, body):
    app = create_app(Settings(disable_update_check=True))
    app.dependency_overrides[get_gateway] = lambda: gateway
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        return await client.request(method, path, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{}, {"serial_number": "CONTROL-1"}, {"custom_fields": {"Color": "Red"}}])
@pytest.mark.parametrize(
    ("selection", "batch_location", "expected"),
    [
        ({"parent_id": "box", "location_id": "cabinet"}, "room", "box"),
        ({"parent_id": "box"}, "cabinet", "box"),
        ({"location_id": "cabinet"}, "room", "cabinet"),
        ({}, "cabinet", "cabinet"),
        ({"parent_id": "box"}, None, "box"),
        ({"parent_id": "", "location_id": "cabinet"}, "room", "cabinet"),
        ({}, None, None),
    ],
)
async def test_create_uses_the_same_parent_before_and_after_enrichment(
    gateway, extra, selection, batch_location, expected
):
    gateway.get_item.return_value["parent"] = {"id": expected} if expected else None
    response = await send(
        gateway,
        "POST",
        "/api/items",
        {"location_id": batch_location, "items": [{"name": "Control", "tag_ids": ["tag"], **selection, **extra}]},
    )

    assert response.status_code == 200
    assert response.json()["errors"] == []
    assert gateway.create_item.await_args.args[0].parent_id == expected
    assert gateway.create_item.await_args.args[0].tag_ids == ["tag"]
    if extra:
        payload = gateway.update_item.await_args.args[1]
        assert payload["parentId"] == expected
        assert payload["tagIds"] == ["tag"]
        assert gateway.get_item.return_value["fields"][0] in payload["fields"]
    else:
        gateway.update_item.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_field_update_keeps_existing_field_identity(gateway):
    duplicate = {"id": "duplicate", "name": "Source", "type": "text", "textValue": "Keep this value"}
    gateway.get_item.return_value["fields"].append(duplicate)
    response = await send(
        gateway,
        "POST",
        "/api/items",
        {"items": [{"name": "Control", "parent_id": "box", "custom_fields": {"Source": "Manual", "Empty": ""}}]},
    )

    assert response.status_code == 200
    assert gateway.update_item.await_args.args[1]["fields"] == [
        {"id": "field", "name": "Source", "type": "text", "textValue": "Manual"},
        duplicate,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", [{"id": "box"}, None])
async def test_update_keeps_parent_tags_and_unrelated_fields(gateway, parent):
    gateway.get_item.return_value["parent"] = parent
    response = await send(
        gateway,
        "PUT",
        "/api/items/item",
        {"name": "Updated control", "parentId": "unrequested-move", "fields": [], "insured": False},
    )

    assert response.status_code == 200
    payload = gateway.update_item.await_args.args[1]
    assert payload["name"] == "Updated control"
    assert payload["parentId"] == (parent["id"] if parent else None)
    assert payload["tagIds"] == ["tag"]
    assert "syncChildEntityLocations" not in payload
    assert "attachments" not in payload
    for key in ("description", "quantity", "serialNumber", "manufacturer", "assetId", "insured", "fields"):
        assert payload[key] == gateway.get_item.return_value[key]
