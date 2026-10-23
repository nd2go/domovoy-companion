"""Check changed Companion code against a disposable native Homebox."""

import httpx
import pytest
from conftest import HomeboxTestAccount
from pydantic import SecretStr

from homebox_companion.core import persistent_settings
from homebox_companion.core.config import Settings
from server.app import create_app

pytestmark = pytest.mark.live


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_key", [False, True])
async def test_schema_templates_values_and_existing_save(
    homebox_test_account: HomeboxTestAccount, tmp_path, monkeypatch, homebox_key_factory, configured_key
):
    monkeypatch.setattr(persistent_settings, "DATA_DIR", tmp_path)
    account = homebox_test_account
    api_key = SecretStr(homebox_key_factory().token) if configured_key else None
    app = create_app(
        Settings(
            homebox_url=account.server.base_url,
            homebox_api_key=api_key,
            disable_update_check=True,
            llm_api_key="test-no-model",
        )
    )
    headers = {"X-Companion-Request": "1"}
    if not configured_key:
        headers["Authorization"] = "Bearer " + account.control_token
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://companion.test", headers=headers
        ) as client:
            initial = await client.get("/api/settings/inventory-schema")
            assert initial.status_code == 200
            state = initial.json()
            applied = await client.put(
                "/api/settings/inventory-schema", json={"expected_revision": 0, "schema": state["schema"]}
            )
            assert applied.status_code == 200, applied.text
            assert applied.json()["revision"] == 1
            room = await client.post("/api/locations", json={"name": "DATA03 test location"})
            assert room.status_code == 200
            parent = room.json()["id"]
            values = {
                "diameter_mm": "2,5",
                "thread": "M5",
                "waterproof": "false",
                "expiry_date": "2028-02-29",
                "applications": '["wood","metal"]',
                "owner_note": "Keep me",
                "source_note": "manual: synthetic control",
            }
            bad = await client.post(
                "/api/items",
                json={
                    "items": [
                        {
                            "name": "Invalid DATA03 item",
                            "parent_id": parent,
                            "schema_revision": 0,
                            "category_id": "screw",
                            "custom_fields": values,
                        }
                    ]
                },
            )
            assert bad.status_code == 409
            created = await client.post(
                "/api/items",
                json={
                    "items": [
                        {
                            "name": "DATA03 item",
                            "parent_id": parent,
                            "schema_revision": 1,
                            "category_id": "screw",
                            "custom_fields": values,
                        }
                    ]
                },
            )
            assert created.status_code == 200, created.text
            item_id = created.json()["created"][0]["id"]
            async with httpx.AsyncClient(
                base_url=account.api_url, headers={"Authorization": "Bearer " + account.control_token}, timeout=15
            ) as direct:
                templates = (await direct.get("/templates")).json()
                assert len([x for x in templates if x["description"].startswith("domovoy:schema:v1:")]) == len(
                    state["schema"]["categories"]
                )
                stored = (await direct.get("/entities/" + item_id)).json()
                saved = {x["name"]: x["textValue"] for x in stored["fields"]}
                assert saved["diameter_mm"] == "2.5"
                assert saved["thread"] == "M5"
                assert saved["waterproof"] == "false"
                assert saved["expiry_date"] == "2028-02-29"
                assert saved["applications"] == '["wood","metal"]'
                assert saved["owner_note"] == "Keep me"
                assert saved["inventory_category"] == "screw"
                assert stored["parent"]["id"] == parent
                schema = applied.json()["schema"]
                schema["fields"]["diameter_mm"]["label"] = "Renamed diameter"
                again = await client.put(
                    "/api/settings/inventory-schema", json={"expected_revision": 1, "schema": schema}
                )
                assert again.status_code == 200, again.text
                after = (await direct.get("/entities/" + item_id)).json()
                assert after == stored
