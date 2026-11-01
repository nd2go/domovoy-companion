"""Exercise worker transport, profile compatibility, cancellation, and admission."""

import asyncio
import base64
import hashlib
import io
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from PIL import Image
from pydantic import BaseModel, SecretStr

from homebox_companion.ai import llm, worker_client
from homebox_companion.ai.vision_contract import VisionBackendError
from homebox_companion.ai.vision_profiles import resolve_vision_profile
from homebox_companion.ai.worker_client import WorkerCall, WorkerScope, current_call
from homebox_companion.core import config, persistent_settings
from homebox_companion.core.persistent_settings import ModelProfile, PersistentSettings, ProfileStatus
from server.api import llm_profiles
from server.api.tools.vision import router as vision_router
from server.dependencies import get_verified_gateway, require_auth
from server.vision_runtime import VisionAdmissionMiddleware, bind_worker_context


class Item(BaseModel):
    quantity: int
    model: str | None


@pytest.fixture
def setup_worker(monkeypatch, tmp_path):
    settings = config.Settings(
        _env_file=None,
        vision_backend="codex_cli",
        vision_worker_url="http://127.0.0.1:8769",
        vision_worker_token=SecretStr("test-worker-credential"),
        vision_homebox_instance=str(uuid4()),
    )
    monkeypatch.setattr(config, "settings", settings)
    monkeypatch.setattr(persistent_settings, "SETTINGS_FILE", tmp_path / "settings.yaml")
    monkeypatch.setattr(persistent_settings, "DATA_DIR", tmp_path)
    persistent_settings.clear_settings_cache()
    persistent_settings.save_settings(
        PersistentSettings(
            llm_profiles=[ModelProfile(name="codex", model="", backend="codex_cli", status=ProfileStatus.PRIMARY)]
        )
    )
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buffer, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    scope = WorkerScope(
        environment="dev",
        homebox_instance=settings.vision_homebox_instance,
        collection_id=uuid4(),
        draft_id=uuid4(),
        analysis_revision=2,
    )
    call = WorkerCall(uuid4(), resolve_vision_profile().revision, time.monotonic() + 120, scope, "owner")
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-worker-credential"
        if request.url.path.startswith("/v1/readiness/"):
            return httpx.Response(
                200,
                json={
                    "version": "1",
                    "environment": "dev",
                    "backend": "codex_cli",
                    "ready": True,
                    "vision": True,
                    "multi_image": True,
                    "structured_output": True,
                },
            )
        if request.url.path == "/v1/cancel":
            return httpx.Response(200, json={"cancelled": True})
        value = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "version": "1",
                "context": scope.model_dump(mode="json"),
                "vision": {
                    "version": "1",
                    "request_id": value["vision"]["request_id"],
                    "backend": "codex_cli",
                    "data": {"quantity": 1, "model": None},
                },
            },
        )

    real_client = httpx.AsyncClient
    handler = SimpleNamespace(handle=handle)

    def client_factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(lambda request: handler.handle(request)), **kwargs)

    monkeypatch.setattr(
        worker_client,
        "httpx",
        SimpleNamespace(
            AsyncClient=client_factory,
            HTTPError=httpx.HTTPError,
            TimeoutException=httpx.TimeoutException,
            Limits=httpx.Limits,
        ),
    )
    api = AsyncMock(side_effect=AssertionError("No API call is allowed"))
    monkeypatch.setattr(llm, "json_completion", api)
    yield SimpleNamespace(settings=settings, uri=uri, scope=scope, call=call, calls=calls, handler=handler, api=api)
    persistent_settings.clear_settings_cache()


async def complete(fixture):
    token = current_call.set(fixture.call)
    try:
        return await llm.vision_completion("Inspect", "Return JSON", [fixture.uri], response_model=Item)
    finally:
        current_call.reset(token)


@pytest.mark.asyncio
async def test_real_adapter_correlates_envelope_and_hashes(setup_worker):
    f = setup_worker
    assert await complete(f) == {"quantity": 1, "model": None}
    payload = json.loads(f.calls[-1].content)
    assert payload["vision"]["request_id"] == str(f.call.request_id)
    assert payload["photo_sha256"] == [hashlib.sha256(base64.b64decode(f.uri.split(",")[1])).hexdigest()]
    assert {key: payload[key] for key in type(f.scope).model_fields} == f.scope.model_dump(mode="json")
    assert payload["vision"]["model"] is None
    assert "test-worker-credential" not in json.dumps(payload)
    f.api.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["collection_id", "draft_id", "analysis_revision", "request_id", "backend", "quantity"]
)
async def test_changed_context_identity_or_type_is_rejected(setup_worker, field):
    f = setup_worker
    original = f.handler.handle

    def changed(request):
        response = original(request)
        if request.url.path == "/v1/vision":
            value = response.json()
            if field in {"collection_id", "draft_id"}:
                value["context"][field] = str(uuid4())
            elif field == "analysis_revision":
                value["context"][field] = True
            elif field == "quantity":
                value["vision"]["data"][field] = "1"
            else:
                value["vision"][field] = str(uuid4()) if field == "request_id" else "litellm"
            return httpx.Response(200, json=value)
        return response

    f.handler.handle = changed
    with pytest.raises(VisionBackendError) as error:
        await complete(f)
    assert error.value.code == "invalid_response"
    f.api.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,status", [("auth_required", 503), ("busy", 409), ("quota", 429), ("timeout", 504), ("cancelled", 409)]
)
async def test_failure_is_fixed_and_never_calls_api(setup_worker, reason, status):
    f = setup_worker
    f.handler.handle = lambda _: httpx.Response(status, json={"reason": reason, "provider_text": "private-secret"})
    with pytest.raises(VisionBackendError) as error:
        await complete(f)
    assert error.value.status_code == status
    assert "private-secret" not in str(error.value)
    f.api.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_reply_and_redirect_are_not_followed(setup_worker, monkeypatch):
    f = setup_worker
    original = f.handler.handle

    def large(request):
        return httpx.Response(200, content=b"x" * 100) if request.url.path == "/v1/vision" else original(request)

    f.handler.handle = large
    monkeypatch.setattr(worker_client, "MAX_REPLY_BYTES", 50)
    with pytest.raises(VisionBackendError, match="response"):
        await complete(f)
    f.handler.handle = lambda _: httpx.Response(307, headers={"location": "http://example.invalid"}, json={})
    with pytest.raises(VisionBackendError):
        await complete(f)
    assert all(request.url.host == "127.0.0.1" for request in f.calls)


@pytest.mark.asyncio
async def test_cancel_sends_exact_context_and_releases_adapter(setup_worker):
    f = setup_worker
    entered = asyncio.Event()
    original = f.handler.handle

    async def waiting(request):
        if request.url.path == "/v1/vision":
            entered.set()
            await asyncio.Event().wait()
        return original(request)

    f.handler.handle = waiting
    task = asyncio.create_task(complete(f))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert f.call.adapter is None
    cancel = json.loads(f.calls[-1].content)
    assert cancel == {**f.scope.model_dump(mode="json"), "request_id": str(f.call.request_id)}


@pytest.mark.asyncio
async def test_snapshot_keeps_cli_and_rejects_profile_change(setup_worker):
    f = setup_worker
    original = f.handler.handle

    def changed(request):
        response = original(request)
        if request.url.path == "/v1/vision":
            persistent_settings.save_settings(
                PersistentSettings(
                    llm_profiles=[
                        ModelProfile(
                            name="api",
                            model="gpt-5-mini",
                            backend="litellm",
                            status=ProfileStatus.PRIMARY,
                            api_key=SecretStr("unused-key"),
                        )
                    ]
                )
            )
        return response

    f.handler.handle = changed
    with pytest.raises(VisionBackendError) as error:
        await complete(f)
    assert error.value.reason == "profile_changed"
    f.api.assert_not_awaited()


def test_profiles_round_trip_without_inheriting_api_model(setup_worker):
    f = setup_worker
    assert resolve_vision_profile().model is None
    assert resolve_vision_profile().backend == "codex_cli"
    f.settings.vision_backend = "litellm"
    assert resolve_vision_profile().backend == "codex_cli"
    old = ModelProfile(name="old", model="api-model", status=ProfileStatus.PRIMARY)
    persistent_settings.save_settings(PersistentSettings(llm_profiles=[old]))
    assert resolve_vision_profile().backend == "litellm"
    f.settings.vision_backend = "codex_cli"
    assert resolve_vision_profile().backend == "codex_cli" and resolve_vision_profile().model is None
    with pytest.raises(ValueError, match="fallback"):
        PersistentSettings(
            llm_profiles=[old, ModelProfile(name="bad", model="", backend="codex_cli", status=ProfileStatus.FALLBACK)]
        )


@pytest.mark.asyncio
async def test_profile_readiness_and_cli_test_make_no_model_calls(setup_worker):
    f = setup_worker
    ready = await llm_profiles.active_readiness()
    tested = await llm_profiles.test_profile_connection("codex")
    assert ready["ready"] and ready["max_parallelism"] == 1 and not ready["chat_available"]
    assert tested.success
    assert all(request.url.path == "/v1/readiness/codex_cli" for request in f.calls)
    assert "test-worker-credential" not in json.dumps(ready)


@pytest.mark.asyncio
async def test_admission_rejects_busy_before_reading_second_body(setup_worker):
    f = setup_worker
    app = FastAPI()
    app.add_middleware(VisionAdmissionMiddleware, settings=f.settings)
    entered = asyncio.Event()
    release = asyncio.Event()

    @app.post("/api/tools/vision/detect")
    async def detect(request: Request):
        await request.body()
        entered.set()
        await release.wait()
        return {"ok": True}

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    async with client:
        first = asyncio.create_task(
            client.post("/api/tools/vision/detect", headers={"X-Request-ID": str(uuid4())}, content=b"photo")
        )
        await asyncio.wait_for(entered.wait(), 2)

        async def untouched():
            pytest.fail("The second body must not be read")
            yield b""

        second = await client.post(
            "/api/tools/vision/detect", headers={"X-Request-ID": str(uuid4())}, content=untouched()
        )
        assert second.status_code == 409 and second.json()["code"] == "VISION_BUSY"
        release.set()
        assert (await first).status_code == 200
    assert app.state.vision_active is None


@pytest.mark.asyncio
async def test_foreign_owner_cannot_cancel_active_request(setup_worker, monkeypatch):
    f = setup_worker
    from server.api import inventory_schema

    monkeypatch.setattr(inventory_schema, "homebox", AsyncMock(return_value={"id": str(f.scope.collection_id)}))
    app = FastAPI()
    app.include_router(vision_router, prefix="/vision")
    app.state.vision_active = f.call
    gateway = SimpleNamespace(access=SimpleNamespace(identity_scope="other-owner", group_id=str(f.scope.collection_id)))
    app.dependency_overrides[get_verified_gateway] = lambda: gateway
    body = {"request_id": str(f.call.request_id), "draft_id": str(f.scope.draft_id), "analysis_revision": 2}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/vision/cancel", json=body)).json() == {"cancelled": False}
        assert not f.call.cancelled.is_set()
        gateway.access.identity_scope = "owner"
        assert (await client.post("/vision/cancel", json=body)).json() == {"cancelled": True}
        assert f.call.cancelled.is_set()


@pytest.mark.asyncio
async def test_foreign_collection_is_rejected_before_worker(setup_worker, monkeypatch):
    from fastapi import HTTPException

    from server.api import inventory_schema

    f = setup_worker
    gateway = SimpleNamespace(access=SimpleNamespace(group_id=str(f.scope.collection_id), identity_scope="owner"))
    monkeypatch.setattr(inventory_schema, "homebox", AsyncMock(return_value={"id": str(uuid4())}))
    request = Request({"type": "http", "headers": []})
    token = current_call.set(f.call)
    try:
        with pytest.raises(HTTPException) as error:
            await bind_worker_context(request, gateway)
        assert error.value.status_code == 403
        assert f.calls == []
    finally:
        current_call.reset(token)


@pytest.mark.asyncio
async def test_direct_chat_is_rejected_before_model_or_tool(setup_worker):
    from fastapi.responses import JSONResponse

    from homebox_companion.chat.session import ChatSession
    from server.api.chat import router as chat_router
    from server.dependencies import get_bound_executor, get_session

    f = setup_worker
    app = FastAPI()
    app.state.settings = f.settings
    app.include_router(chat_router, prefix="/api")
    app.dependency_overrides[get_session] = lambda: ChatSession()
    app.dependency_overrides[get_bound_executor] = lambda: SimpleNamespace(execution_token="unused")

    @app.exception_handler(VisionBackendError)
    async def error_handler(request, error):
        return JSONResponse({"code": error.error_code}, status_code=error.status_code)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/chat/messages", json={"message": "Do not call the API"})
    assert response.status_code == 503 and response.json()["code"] == "VISION_CHAT_UNAVAILABLE"
    assert f.calls == []
    f.api.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_crud_keeps_legacy_data_and_rejects_cli_fallback(setup_worker):
    f = setup_worker
    app = FastAPI()
    app.include_router(llm_profiles.router, prefix="/api")
    app.dependency_overrides[require_auth] = lambda: None
    before = persistent_settings.SETTINGS_FILE.read_bytes()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        bad = await client.post("/api/llm/profiles", json={"name": "bad", "backend": "codex_cli", "status": "fallback"})
        assert bad.status_code == 422
        assert persistent_settings.SETTINGS_FILE.read_bytes() == before
        saved = await client.post(
            "/api/llm/profiles", json={"name": "explicit-api", "model": "api-model", "backend": "litellm"}
        )
        assert saved.status_code == 201 and saved.json()["backend"] == "litellm"
        switched = await client.post("/api/llm/profiles/explicit-api/activate")
        assert switched.status_code == 200
        assert resolve_vision_profile().backend == "litellm"
        result = await client.get("/api/llm/profiles")
        assert len(result.json()["profiles"]) == 2
    assert f.calls == []


def test_codex_schema_keeps_nullable_types_and_property_names():
    from homebox_companion.ai.worker_client import codex_schema

    schema = {
        "type": "object",
        "properties": {"default": {"type": ["string", "null"], "default": None}},
        "$defs": {"child": {"type": "object", "properties": {"model": {"type": ["string", "null"]}}}},
    }
    original = json.loads(json.dumps(schema))
    strict = codex_schema(schema)
    assert schema == original
    assert strict["required"] == ["default"] and strict["additionalProperties"] is False
    assert strict["properties"]["default"] == {"type": ["string", "null"]}
    assert strict["$defs"]["child"]["required"] == ["model"]
    assert strict["$defs"]["child"]["additionalProperties"] is False
    with pytest.raises(VisionBackendError):
        codex_schema({"type": "object", "additionalProperties": {"type": "string"}})


def test_failed_settings_replace_preserves_the_cli_selection(setup_worker, monkeypatch):
    before = persistent_settings.SETTINGS_FILE.read_bytes()

    def unavailable(*args):
        raise OSError("Controlled replacement failure")

    monkeypatch.setattr(persistent_settings.os, "replace", unavailable)
    with pytest.raises(OSError):
        persistent_settings.save_settings(
            PersistentSettings(
                llm_profiles=[
                    ModelProfile(name="new-api", model="other", backend="litellm", status=ProfileStatus.PRIMARY)
                ]
            )
        )
    assert persistent_settings.SETTINGS_FILE.read_bytes() == before
    assert list(persistent_settings.DATA_DIR.glob(".settings-*")) == []
    persistent_settings.clear_settings_cache()
    assert resolve_vision_profile().backend == "codex_cli"
