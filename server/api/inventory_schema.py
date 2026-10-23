"""Collection-scoped inventory definitions and Homebox template projection."""

import asyncio
import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from homebox_companion import HomeboxGateway
from homebox_companion.core import persistent_settings
from homebox_companion.core.inventory_schema import (
    MAX_BYTES,
    SchemaError,
    check_evolution,
    effective_fields,
    encode_value,
    normalize_values,
)

from ..dependencies import get_verified_gateway


async def capacity(request: Request):
    active = getattr(request.app.state, "inventory_schema_active", 0)
    if active >= 4:
        raise HTTPException(429, "Too many inventory schema operations")
    request.app.state.inventory_schema_active = active + 1
    try:
        yield
    finally:
        request.app.state.inventory_schema_active -= 1


router = APIRouter(prefix="/settings/inventory-schema", dependencies=[Depends(capacity)])
Gateway = Annotated[HomeboxGateway, Depends(get_verified_gateway)]
SEED = files("homebox_companion").joinpath("inventory_schema_seed.json")
TEMPLATE_MARKER = "domovoy:schema:v1:"


async def bounded_json(request: Request) -> dict:
    data = bytearray()
    try:
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_BYTES:
                    raise HTTPException(413, "Schema request exceeds 2 MiB")
                data.extend(chunk)
        value = json.loads(data)
    except ValueError, RecursionError:
        raise HTTPException(422, "Invalid JSON") from None
    except TimeoutError:
        raise HTTPException(408, "Schema request timed out") from None
    if not isinstance(value, dict):
        raise HTTPException(422, "Expected an object")
    return value


async def homebox(gateway: HomeboxGateway, path: str, method="GET", payload=None):
    client = gateway.transport_client
    headers = {
        "Authorization": "Bearer " + gateway.access.credential.get_secret_value(),
        "X-Tenant": gateway.access.group_id or "",
    }
    data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
    if data is not None and len(data) > MAX_BYTES:
        raise HTTPException(413, "Template exceeds 2 MiB")
    headers["Content-Type"] = "application/json"
    try:
        async with asyncio.timeout(15):
            async with client.client.stream(
                method, client.base_url + path, headers=headers, content=data, timeout=10, follow_redirects=False
            ) as response:
                if response.status_code not in (200, 201):
                    status = response.status_code if response.status_code in (401, 403, 404) else 502
                    raise HTTPException(status, "Homebox rejected the schema operation; reconcile before retry")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > MAX_BYTES:
                        raise HTTPException(502, "Homebox response exceeds the schema limit")
                    raw.extend(chunk)
        return json.loads(raw)
    except httpx.HTTPError, ValueError, RecursionError:
        raise HTTPException(502, "Homebox schema operation failed; reconcile before retry") from None


async def scope(gateway: HomeboxGateway) -> str:
    group = await homebox(gateway, "/groups")
    group_id = gateway.access.group_id or (group.get("id") if isinstance(group, dict) else None)
    if not isinstance(group, dict) or not isinstance(group_id, str) or not group_id or group.get("id") != group_id:
        raise HTTPException(403, "Homebox collection does not match the request")
    key = gateway.transport_client.base_url.rstrip("/") + "\n" + group_id
    return hashlib.sha256(key.encode()).hexdigest()


def directory() -> Path:
    path = persistent_settings.DATA_DIR / "inventory-schemas"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def read_state(key: str) -> dict:
    path = directory() / (key + ".json")
    if not path.exists():
        return {"revision": 0, "schema": json.loads(SEED.read_text())}
    if path.is_symlink() or path.stat().st_size > MAX_BYTES + 1024:
        raise HTTPException(500, "Invalid schema configuration file")
    return json.loads(path.read_text())


@contextmanager
def schema_lock(key):
    path = directory() / (key + ".lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(409, "Another schema update is running") from None
        yield
    finally:
        os.close(fd)


def write_state(key, state):
    fd, temporary = tempfile.mkstemp(dir=directory(), prefix=".pending-")
    try:
        with os.fdopen(fd, "w") as target:
            json.dump(state, target, ensure_ascii=False, allow_nan=False)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, directory() / (key + ".json"))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def candidate(body, state):
    if set(body) != {"expected_revision", "schema"} or type(body.get("expected_revision")) is not int:
        raise HTTPException(422, "Expected a schema and integer expected_revision")
    if body["expected_revision"] != state["revision"]:
        raise HTTPException(409, "Schema revision changed; reload definitions and keep your edits")
    try:
        check_evolution(state["schema"], body["schema"])
        for key in body["schema"]["categories"]:
            if len(set(effective_fields(body["schema"], key)) | {"inventory_category", "quantity_unit"}) > 100:
                raise SchemaError("A template can contain at most 100 fields")
    except SchemaError as exc:
        raise HTTPException(422, str(exc)) from None
    return body["schema"]


def template_fields(schema, category_id):
    keys = effective_fields(schema, category_id)
    values = {key: "" for key in keys if not schema["fields"][key].get("archived")}
    values.update(inventory_category=category_id, quantity_unit="pcs")
    return [{"name": key, "type": "text", "textValue": value} for key, value in values.items()]


async def project_templates(gateway, schema):
    summaries = await homebox(gateway, "/templates")
    if not isinstance(summaries, list) or len(summaries) > 1000:
        raise HTTPException(502, "Invalid or excessive Homebox templates")
    existing = {}
    for summary in summaries:
        if not isinstance(summary, dict) or not isinstance(summary.get("description", ""), str):
            raise HTTPException(502, "Invalid Homebox template")
        marker = summary.get("description", "")
        if marker.startswith(TEMPLATE_MARKER):
            if marker in existing:
                raise HTTPException(409, "Duplicate managed templates require reconciliation")
            existing[marker] = summary
    for key, category in schema["categories"].items():
        if category.get("archived"):
            continue
        marker = TEMPLATE_MARKER + key
        fields = template_fields(schema, key)
        old = existing.get(marker)
        if old is None:
            await homebox(
                gateway,
                "/templates",
                "POST",
                {
                    "name": f"Domovoy: {category['label'][:175]} [{key}]",
                    "description": marker,
                    "defaultQuantity": 1,
                    "fields": fields,
                },
            )
            continue
        try:
            template_id = str(UUID(old["id"]))
        except KeyError, ValueError, TypeError, AttributeError:
            raise HTTPException(502, "Invalid Homebox template ID") from None
        current = await homebox(gateway, "/templates/" + template_id)
        if not isinstance(current, dict) or not isinstance(current.get("fields"), list):
            raise HTTPException(502, "Invalid Homebox template fields")
        managed = {field["name"]: field for field in fields}
        merged = []
        for field in current["fields"]:
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                raise HTTPException(502, "Invalid Homebox template field")
            name = field["name"]
            if name in managed:
                wanted = managed.pop(name)
                if field.get("type") != "text":
                    raise HTTPException(409, "Managed template field type changed; reconcile before applying")
                merged.append({**wanted, **field})
            elif name not in schema["fields"]:
                merged.append(field)
        merged.extend(managed.values())
        payload = {k: v for k, v in current.items() if k.startswith("default") or k.startswith("include")}
        payload.pop("defaultLocation", None)
        payload.pop("defaultTags", None)
        payload.update(
            defaultLocationId=(current.get("defaultLocation") or {}).get("id"),
            defaultTagIds=[x["id"] for x in current.get("defaultTags", [])],
            name=f"Domovoy: {category['label'][:175]} [{key}]",
            description=marker,
            notes=current.get("notes", ""),
            fields=merged,
        )
        await homebox(gateway, "/templates/" + template_id, "PUT", payload)


@router.get("")
async def get_schema(gateway: Gateway) -> dict:
    """Read definitions after Homebox verifies the selected collection."""
    return read_state(await scope(gateway))


@router.post("/preview")
async def preview_schema(request: Request, gateway: Gateway) -> dict:
    state = read_state(await scope(gateway))
    schema = candidate(await bounded_json(request), state)
    return {
        "revision": state["revision"],
        "templates": {
            key: template_fields(schema, key)
            for key, value in schema["categories"].items()
            if not value.get("archived")
        },
    }


@router.put("")
async def update_schema(request: Request, gateway: Gateway) -> dict:
    """Apply checked definitions without changing existing inventory values."""
    key = await scope(gateway)
    body = await bounded_json(request)
    with schema_lock(key):
        state = read_state(key)
        schema = candidate(body, state)
        try:
            async with asyncio.timeout(120):
                await project_templates(gateway, schema)
        except TimeoutError:
            raise HTTPException(504, "Template update timed out; reconcile before retry") from None
        state = {"revision": state["revision"] + 1, "schema": schema}
        write_state(key, state)
    return state


@router.post("/values")
async def validate_values(request: Request, gateway: Gateway) -> dict[str, Any]:
    state = read_state(await scope(gateway))
    body = await bounded_json(request)
    if set(body) - {"revision", "category", "values", "input_units"}:
        raise HTTPException(422, "Unknown value request members")
    if type(body.get("revision")) is not int or body["revision"] != state["revision"]:
        raise HTTPException(409, "Schema revision changed")
    schema = state["schema"]
    category = body.get("category")
    if category is not None and (
        not isinstance(category, str)
        or category not in schema["categories"]
        or schema["categories"][category].get("archived")
    ):
        raise HTTPException(422, "Unknown or archived category")
    try:
        values = body.get("values", {})
        units = body.get("input_units", {})
        if not isinstance(units, dict) or len(units) > 100:
            raise SchemaError("Invalid input units")
        normalized = normalize_values(schema, values)
        for field_id, unit in units.items():
            if field_id not in values or field_id not in schema["fields"] or not isinstance(unit, str):
                raise SchemaError("Invalid input unit")
            normalized[field_id] = encode_value(schema, field_id, values[field_id], unit)
        if category is not None:
            normalized["inventory_category"] = category
        return {"revision": state["revision"], "values": normalized}
    except SchemaError as exc:
        raise HTTPException(422, str(exc)) from None


async def check_submission(gateway, items):
    """Validate schema-aware cards before the first inventory write."""
    selected = [item for item in items if item.schema_revision is not None]
    if not selected:
        return
    state = read_state(await scope(gateway))
    for item in selected:
        if item.schema_revision != state["revision"]:
            raise HTTPException(409, "Schema revision changed; review your card before saving")
        category = item.category_id
        schema = state["schema"]
        if category is not None and (
            category not in schema["categories"] or schema["categories"][category].get("archived")
        ):
            raise HTTPException(422, "Unknown or archived category")
        try:
            values = normalize_values(schema, item.custom_fields or {})
        except SchemaError as exc:
            raise HTTPException(422, str(exc)) from None
        if category is not None:
            values["inventory_category"] = category
        item.custom_fields = values
