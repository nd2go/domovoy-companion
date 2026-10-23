"""Exercise schema requests through the real authentication dependencies."""

import copy
import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from test_auth_http_contracts import _contract_client, _settings

from homebox_companion.core import persistent_settings
from server.api import inventory_schema as api


class Homebox:
    def __init__(self):
        self.templates = {}
        self.allowed = True
        self.lost = False
        self.write_count = 0

    def __call__(self, request):
        if not self.allowed:
            return httpx.Response(401)
        if request.url.path.endswith("/users/self"):
            return httpx.Response(200, json={"id": "user-1", "defaultGroupId": "group-1"})
        group = request.headers.get("X-Tenant")
        if group != "group-1":
            return httpx.Response(403)
        if request.url.path.endswith("/groups"):
            return httpx.Response(200, json={"id": group})
        if request.url.path.endswith("/templates"):
            if request.method == "GET":
                return httpx.Response(200, json=list(self.templates.values()))
            value = json.loads(request.content)
            value["id"] = str(uuid4())
            self.templates[value["id"]] = value
            self.write_count += 1
            if self.lost:
                self.lost = False
                raise httpx.ReadError("Lost response")
            return httpx.Response(201, json=value)
        key = request.url.path.rsplit("/", 1)[-1]
        if key in self.templates:
            if request.method == "PUT":
                self.templates[key].update(json.loads(request.content))
                self.write_count += 1
            return httpx.Response(200, json=self.templates[key])
        return httpx.Response(404)


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    monkeypatch.setattr(persistent_settings, "DATA_DIR", tmp_path)
    return Homebox()


def headers(**extra):
    return {"Authorization": "Bearer test-session", "X-Companion-Request": "1", **extra}


@pytest.mark.asyncio
async def test_schema_preview_apply_revision_and_restart(upstream):
    async with _contract_client(_settings(_env_file=None), upstream) as client:
        response = await client.get("/api/settings/inventory-schema", headers=headers())
        assert response.status_code == 200
        state = response.json()
        schema = state["schema"]
        schema["categories"]["new_kind"] = {
            "label": "Новый вид",
            "parent": "tool",
            "blocks": ["dimensions"],
            "fields": [],
        }
        body = {"expected_revision": state["revision"], "schema": schema}
        preview = await client.post("/api/settings/inventory-schema/preview", headers=headers(), json=body)
        assert preview.status_code == 200
        assert not upstream.templates
        applied = await client.put("/api/settings/inventory-schema", headers=headers(), json=body)
        assert applied.status_code == 200
        assert applied.json()["revision"] == 1
        ids = set(upstream.templates)
        assert len(ids) == len(schema["categories"])
        repeated = await client.put("/api/settings/inventory-schema", headers=headers(), json=body)
        assert repeated.status_code == 409
        assert set(upstream.templates) == ids
        values = await client.post(
            "/api/settings/inventory-schema/values",
            headers=headers(),
            json={
                "revision": 1,
                "category": "new_kind",
                "values": {"diameter_mm": "0.6", "owner_note": "keep"},
                "input_units": {"diameter_mm": "cm"},
            },
        )
        assert values.status_code == 200
        assert values.json()["values"] == {"diameter_mm": "6", "owner_note": "keep", "inventory_category": "new_kind"}
    async with _contract_client(_settings(_env_file=None), upstream) as client:
        loaded = await client.get("/api/settings/inventory-schema", headers=headers())
        assert loaded.json() == applied.json()


@pytest.mark.asyncio
async def test_uncertain_projection_reconciles_without_duplicate(upstream):
    async with _contract_client(_settings(_env_file=None), upstream) as client:
        state = (await client.get("/api/settings/inventory-schema", headers=headers())).json()
        body = {"expected_revision": 0, "schema": state["schema"]}
        upstream.lost = True
        failed = await client.put("/api/settings/inventory-schema", headers=headers(), json=body)
        assert failed.status_code == 502
        first_id = next(iter(upstream.templates))
        result = await client.put("/api/settings/inventory-schema", headers=headers(), json=body)
        assert result.status_code == 200
        assert first_id in upstream.templates
        assert len(upstream.templates) == len(state["schema"]["categories"])


@pytest.mark.asyncio
async def test_auth_collection_and_stream_limits(upstream):
    async with _contract_client(_settings(_env_file=None), upstream) as client:
        assert (await client.get("/api/settings/inventory-schema")).status_code == 401
        assert (
            await client.get("/api/settings/inventory-schema", headers=headers(**{"X-Group-Id": "other"}))
        ).status_code == 403
        state = (await client.get("/api/settings/inventory-schema", headers=headers())).json()
        invalid = copy.deepcopy(state["schema"])
        invalid["fields"]["diameter_mm"]["kind"] = []
        result = await client.put(
            "/api/settings/inventory-schema", headers=headers(), json={"expected_revision": 0, "schema": invalid}
        )
        assert result.status_code == 422
        result = await client.put(
            "/api/settings/inventory-schema", headers=headers(), content=b"x" * (api.MAX_BYTES + 1)
        )
        assert result.status_code == 413
        assert not upstream.templates
        upstream.allowed = False
        assert (await client.get("/api/settings/inventory-schema", headers=headers())).status_code == 401


@pytest.mark.asyncio
async def test_preserve_user_template_fields_and_defaults(upstream):
    async with _contract_client(_settings(_env_file=None), upstream) as client:
        state = (await client.get("/api/settings/inventory-schema", headers=headers())).json()
        body = {"expected_revision": 0, "schema": state["schema"]}
        assert (await client.put("/api/settings/inventory-schema", headers=headers(), json=body)).status_code == 200
        template = next(x for x in upstream.templates.values() if x["description"] == api.TEMPLATE_MARKER + "tool")
        field = {"id": str(uuid4()), "name": "owner_field", "type": "text", "textValue": "Keep me"}
        template["fields"].append(field)
        template["defaultName"] = "Keep name"
        next(x for x in template["fields"] if x["name"] == "voltage_v")["textValue"] = "18.5"
        body["schema"]["categories"]["tool"]["label"] = "Renamed tools"
        body["schema"]["fields"]["power_w"]["archived"] = True
        body["expected_revision"] = 1
        assert (await client.put("/api/settings/inventory-schema", headers=headers(), json=body)).status_code == 200
        assert field in template["fields"]
        assert template["defaultName"] == "Keep name"
        assert next(x for x in template["fields"] if x["name"] == "voltage_v")["textValue"] == "18.5"
        assert all(x["name"] != "power_w" for x in template["fields"])


@pytest.mark.asyncio
async def test_streamed_limit_stops_before_projection(upstream):
    chunks_read = []

    async def chunks():
        for i in range(4):
            chunks_read.append(i)
            yield b"x" * (api.MAX_BYTES // 2)

    async with _contract_client(_settings(_env_file=None), upstream) as client:
        response = await client.put("/api/settings/inventory-schema", headers=headers(), content=chunks())
        assert response.status_code == 413
        assert len(chunks_read) == 3
        assert upstream.write_count == 0


@pytest.mark.asyncio
async def test_concurrent_update_lock_keeps_active_revision(upstream):
    async with _contract_client(_settings(_env_file=None), upstream) as client:
        state = (await client.get("/api/settings/inventory-schema", headers=headers())).json()
        key = next(api.directory().glob("*.json"), None)
        assert key is None
        scope_key = hashlib.sha256((_settings(_env_file=None).api_url.rstrip("/") + "\ngroup-1").encode()).hexdigest()
        with api.schema_lock(scope_key):
            result = await client.put(
                "/api/settings/inventory-schema",
                headers=headers(),
                json={"expected_revision": 0, "schema": state["schema"]},
            )
        assert result.status_code == 409
        assert upstream.write_count == 0
        assert (await client.get("/api/settings/inventory-schema", headers=headers())).json()["revision"] == 0
