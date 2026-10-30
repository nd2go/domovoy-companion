"""Check keyless backends, schema validation, readiness, and bounded work."""

import asyncio
import base64
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from pydantic import BaseModel, ValidationError

from homebox_companion.ai import llm, vision_backend, vision_contract
from homebox_companion.ai.vision_contract import (
    BackendKind,
    VisionBackendError,
    VisionReadiness,
    VisionRequest,
    VisionResponse,
    bounded_json,
)
from homebox_companion.core import config
from homebox_companion.core.persistent_settings import CustomFieldDefinition
from homebox_companion.tools.vision.analyzer import analyze_item_details_from_images
from homebox_companion.tools.vision.corrector import correct_item
from homebox_companion.tools.vision.detector import detect_items_from_bytes
from server.api.tools.vision import router
from server.dependencies import VisionContext, get_vision_context

buffer = io.BytesIO()
Image.new("RGB", (8, 8), (10, 20, 30)).save(buffer, format="PNG")
PNG = buffer.getvalue()
URI = "data:image/png;base64," + base64.b64encode(PNG).decode()
ITEM = {"name": "Drill", "quantity": 1, "modelNumber": "CONTROL-1", "ownerNote": "manual: control"}
CUSTOM = [CustomFieldDefinition(name="Owner Note", ai_instruction="Use the manual note when present")]


class FakeBackend:
    model: str | None = None

    def __init__(self, kind: BackendKind = "codex_cli", data=None):
        self.kind = kind
        self.ready = True
        self.data = data
        self.requests = []
        self.delay = 0
        self.stopped = False
        self.response_id: UUID | None = None
        self.response_backend: BackendKind | None = None
        self.failure = None
        self.caps: dict[str, bool] = {}
        self.api = AsyncMock(side_effect=AssertionError("API must not be called"))

    async def readiness(self):
        return VisionReadiness(backend=self.kind, ready=self.ready, **self.caps)

    async def complete(self, request):
        self.requests.append(request)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.failure:
                raise self.failure
            data = self.data
            if data is None:
                assert request.response_schema is not None
                data = (
                    {"items": [copy.deepcopy(ITEM)]}
                    if "items" in request.response_schema["properties"]
                    else copy.deepcopy(ITEM)
                )
            return VisionResponse(
                request_id=self.response_id or request.request_id,
                backend=self.response_backend or self.kind,
                data=data,
            )
        finally:
            self.stopped = True


@pytest.fixture
def selected(monkeypatch):
    settings = config.Settings(_env_file=None, vision_backend="codex_cli", llm_api_key="", openai_api_key="")
    monkeypatch.setattr(config, "settings", settings)
    backend = FakeBackend()
    monkeypatch.setitem(vision_backend.BACKEND_FACTORIES, "codex_cli", lambda _: backend)
    monkeypatch.setattr(llm, "json_completion", backend.api)
    return backend


def request(**changes):
    return VisionRequest(
        backend="codex_cli",
        system_prompt="Inspect the photo",
        user_prompt="Return JSON",
        image_data_uris=[URI],
        **changes,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["codex_cli", "claude_cli"])
async def test_all_three_workflows_share_keyless_contract(selected, monkeypatch, kind):
    selected.kind = kind
    config.settings.vision_backend = kind
    monkeypatch.setitem(vision_backend.BACKEND_FACTORIES, kind, lambda _: selected)
    detected = await detect_items_from_bytes(PNG, "image/png", custom_fields=CUSTOM)
    analyzed = await analyze_item_details_from_images([URI], "Drill", None, custom_fields=CUSTOM)
    corrected = await correct_item(URI, ITEM, "Keep the manual note", custom_fields=CUSTOM)
    assert detected[0].name == analyzed.name == corrected[0].name == "Drill"
    assert all(getattr(item, "owner_note", None) == "manual: control" for item in [detected[0], analyzed, corrected[0]])
    assert all(row.version == "1" and row.backend == kind for row in selected.requests)
    assert len({row.request_id for row in selected.requests}) == 3
    assert all(row.response_schema and len(row.image_data_uris) == 1 for row in selected.requests)
    assert selected.requests[1].image_data_uris == selected.requests[2].image_data_uris == [URI]
    encoded = selected.requests[0].image_data_uris[0].split(",", 1)[1]
    assert Image.open(io.BytesIO(base64.b64decode(encoded))).size == (8, 8)
    assert len({row.system_prompt for row in selected.requests}) == 3
    selected.api.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"items": [{**ITEM, "quantity": "1"}]},
        {"items": [{**ITEM, "name": []}]},
        {"items": [{**ITEM, "ownerNote": 42}]},
    ],
)
async def test_wrong_required_or_custom_type_is_not_success(selected, data):
    selected.data = data
    with pytest.raises(VisionBackendError) as error:
        await detect_items_from_bytes(PNG, "image/png", custom_fields=CUSTOM)
    assert error.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_not_ready_stops_before_inference(selected):
    selected.ready = False
    with pytest.raises(VisionBackendError) as error:
        await llm.vision_completion("inspect", "json", [URI])
    assert error.value.code == "not_ready"
    assert selected.requests == []
    selected.api.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", ["vision", "multi_image", "structured_output"])
async def test_missing_capability_stops_before_inference(selected, cap):
    selected.caps[cap] = False

    class Shape(BaseModel):
        name: str

    with pytest.raises(VisionBackendError):
        await llm.vision_completion("inspect", "json", [URI, URI], response_model=Shape)
    assert selected.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["id", "backend"])
async def test_response_must_match_request(selected, mismatch):
    selected.data = {}
    if mismatch == "id":
        selected.response_id = uuid4()
    else:
        selected.response_backend = "claude_cli"
    with pytest.raises(VisionBackendError) as error:
        await llm.vision_completion("inspect", "json", [URI])
    assert error.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_timeout_cancels_work_and_next_request_runs(selected):
    selected.delay = 60
    with pytest.raises(VisionBackendError) as error:
        await vision_backend.run_vision(request(timeout_seconds=0.01), selected, None)
    assert error.value.code == "timeout"
    assert selected.stopped
    selected.delay = 0
    selected.data = {"ok": True}
    assert await vision_backend.run_vision(request(), selected, None) == {"ok": True}


@pytest.mark.asyncio
async def test_caller_cancellation_is_not_converted_to_success(selected):
    selected.delay = 60
    task = asyncio.create_task(vision_backend.run_vision(request(), selected, None))
    for _ in range(100):
        if selected.requests:
            break
        await asyncio.sleep(0)
    assert selected.requests
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert selected.stopped


@pytest.mark.asyncio
async def test_provider_error_does_not_leak_or_fall_back(selected):
    selected.failure = RuntimeError("secret-token and private/path")
    with pytest.raises(VisionBackendError) as error:
        await llm.vision_completion("inspect", "json", [URI])
    assert error.value.code == "provider_failed"
    assert "secret-token" not in str(error.value)
    assert "private/path" not in error.value.user_message
    selected.api.assert_not_awaited()


@pytest.mark.asyncio
async def test_uninstalled_selected_adapter_does_not_call_api(selected, monkeypatch):
    config.settings.vision_backend = "claude_cli"
    monkeypatch.delitem(vision_backend.BACKEND_FACTORIES, "claude_cli", raising=False)
    with pytest.raises(VisionBackendError) as error:
        await llm.vision_completion("inspect", "json", [URI])
    assert error.value.code == "not_ready"
    assert selected.requests == []
    selected.api.assert_not_awaited()


@pytest.mark.asyncio
async def test_factory_cannot_change_selected_backend(selected):
    selected.kind = "claude_cli"
    with pytest.raises(VisionBackendError):
        await llm.vision_completion("inspect", "json", [URI])
    assert selected.requests == []


@pytest.mark.parametrize(
    "uri", ["https://example.invalid/photo.png", "file:///etc/passwd", "data:image/png;base64,!!!"]
)
def test_external_paths_urls_and_bad_encoding_are_not_photo_input(uri):
    with pytest.raises(ValidationError):
        VisionRequest(backend="codex_cli", system_prompt="x", user_prompt="x", image_data_uris=[uri])


def test_image_budgets_before_base64_decode(monkeypatch):
    monkeypatch.setattr(vision_contract, "MAX_TOTAL_IMAGE_BYTES", 1)
    monkeypatch.setattr(vision_contract.base64, "b64decode", lambda *args, **kwargs: pytest.fail("must not decode"))
    with pytest.raises(ValidationError):
        request()


def test_individual_image_budget(monkeypatch):
    monkeypatch.setattr(vision_contract, "MAX_IMAGE_BYTES", 1)
    with pytest.raises(ValidationError):
        request()


def test_image_count_limit():
    with pytest.raises(ValidationError):
        VisionRequest(backend="codex_cli", system_prompt="x", user_prompt="x", image_data_uris=[URI] * 31)


def test_schema_limit(monkeypatch):
    monkeypatch.setattr(vision_contract, "MAX_SCHEMA_BYTES", 30)
    with pytest.raises(ValidationError):
        request(response_schema={"description": "x" * 100})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, 301])
def test_timeout_contract(value):
    with pytest.raises(ValidationError):
        request(timeout_seconds=value)


def test_json_text_depth_and_structure_limits():
    with pytest.raises(ValueError):
        bounded_json({"x": "y" * 101}, 100)
    with pytest.raises(ValueError):
        bounded_json({"x": float("nan")}, 100)
    nested = []
    for _ in range(70):
        nested = [nested]
    with pytest.raises(ValueError):
        bounded_json(nested, 10000)
    with pytest.raises(ValueError):
        bounded_json([[]] * 65536, 1000000)


@pytest.mark.asyncio
async def test_result_size_limit(selected, monkeypatch):
    monkeypatch.setattr(vision_contract, "MAX_RESULT_BYTES", 30)
    selected.data = {"large": "x" * 100}
    with pytest.raises(VisionBackendError):
        await llm.vision_completion("inspect", "json", [URI])


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_http_route_has_keyless_readiness_and_no_write_on_invalid_data(selected, ready):
    selected.ready = ready
    selected.data = {"items": [{**ITEM, "quantity": []}]}
    gateway = AsyncMock()
    app = FastAPI()
    app.include_router(router, prefix="/vision")
    app.dependency_overrides[get_vision_context] = lambda: VisionContext(gateway, [], None, None, None, CUSTOM)

    @app.exception_handler(VisionBackendError)
    async def failure_handler(request, error):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=error.status_code, content={"code": error.code})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/vision/detect", files={"image": ("control.png", PNG, "image/png")})
    assert result.status_code >= 400
    assert "items" not in result.json()
    assert not gateway.mock_calls
    assert bool(selected.requests) == ready


def test_request_and_response_are_json_round_trippable():
    value = request(response_schema={"type": "object"})
    assert VisionRequest.model_validate_json(value.model_dump_json()) == value
    response = VisionResponse(request_id=value.request_id, backend=value.backend, data={"number": 2.5, "unknown": None})
    assert VisionResponse.model_validate_json(response.model_dump_json()) == response
    assert json.loads(response.model_dump_json())["data"] == {"number": 2.5, "unknown": None}


@pytest.mark.asyncio
async def test_explicit_request_id_timeout_and_model_are_separate(selected):
    selected.data = {"ok": True}
    request_id = uuid4()
    await llm.vision_completion("inspect", "json", [URI], request_id=request_id, timeout_seconds=0.5)
    sent = selected.requests[0]
    assert sent.request_id == request_id and sent.timeout_seconds == 0.5
    assert sent.model is None
    assert "api_key" not in sent.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("native_schema", [True, False])
async def test_legacy_api_keeps_model_format_negotiation(monkeypatch, native_schema):
    from types import SimpleNamespace

    from homebox_companion.core import llm_utils
    from homebox_companion.core.persistent_settings import ModelProfile

    class Shape(BaseModel):
        name: str

    settings = config.Settings(_env_file=None)
    assert settings.vision_backend == "litellm"
    assert ModelProfile(name="old-profile", model="synthetic-vision").model == "synthetic-vision"
    monkeypatch.setattr(config, "settings", settings)
    monkeypatch.setattr(
        llm_utils,
        "resolve_llm_credentials",
        lambda: llm_utils.LLMCredentials("synthetic-vision", "synthetic-key", None),
    )
    monkeypatch.setattr(
        llm,
        "get_model_capabilities",
        lambda _: SimpleNamespace(vision=True, multi_image=True, structured_output=native_schema),
    )
    api = AsyncMock(return_value={"name": "Drill"})
    monkeypatch.setattr(llm, "json_completion", api)
    assert await llm.vision_completion("inspect", "json", [URI], response_model=Shape) == {"name": "Drill"}
    assert api.call_args.kwargs["response_format"] == (Shape if native_schema else None)
    assert api.call_args.kwargs["max_response_bytes"] == vision_contract.MAX_RESULT_BYTES


@pytest.mark.asyncio
async def test_late_backend_cannot_return_success(selected, monkeypatch):
    import time

    async def late(request):
        time.sleep(0.02)
        return VisionResponse(request_id=request.request_id, backend=request.backend, data={"ok": True})

    monkeypatch.setattr(selected, "complete", late)
    with pytest.raises(VisionBackendError) as error:
        await vision_backend.run_vision(request(timeout_seconds=0.001), selected, None)
    assert error.value.code == "timeout"


def test_failure_wire_contract_has_no_provider_text():
    failure = vision_contract.VisionFailure(request_id=uuid4(), backend="claude_cli", code="not_ready")
    assert vision_contract.VisionFailure.model_validate_json(failure.model_dump_json()) == failure
    with pytest.raises(ValidationError):
        vision_contract.VisionFailure.model_validate({**failure.model_dump(), "provider_message": "secret"})


def test_contract_refuses_command_credentials_and_unknown_version():
    value = request().model_dump()
    for extra in [{"command": "id"}, {"api_key": "secret"}, {"version": "2"}]:
        with pytest.raises(ValidationError):
            VisionRequest.model_validate({**value, **extra})


@pytest.mark.asyncio
async def test_schema_requires_a_consumer_validator_before_inference(selected):
    with pytest.raises(VisionBackendError) as error:
        await vision_backend.run_vision(request(response_schema={"type": "object"}), selected, None)
    assert error.value.code == "invalid_input"
    assert not selected.requests


def test_published_schemas_and_examples_match_runtime_contract():
    contract = Path(__file__).resolve().parents[1] / "docs/contracts/vision-v1"
    for name, model in [
        ("request", VisionRequest),
        ("response", VisionResponse),
        ("failure", vision_contract.VisionFailure),
    ]:
        assert json.loads((contract / f"{name}.schema.json").read_text()) == model.model_json_schema()
        for example in (contract / "examples").glob(f"*.{name}.json"):
            model.model_validate_json(example.read_text())
    for kind in ["litellm", "codex_cli", "claude_cli"]:
        example = VisionRequest.model_validate_json((contract / "examples" / f"{kind}.request.json").read_text())
        assert example.backend == kind


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["first", "repair"])
async def test_api_byte_limit_stops_parsing_and_further_calls(monkeypatch, stage):
    import importlib

    from homebox_companion.core.exceptions import LLMServiceError

    module = importlib.import_module("homebox_companion.ai.json_completion")
    large = "é" * 16

    def response(content):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)

    replies = [response(large)] if stage == "first" else [response("{"), response(large)]
    router = SimpleNamespace(acompletion=AsyncMock(side_effect=replies))
    parse = Mock(wraps=module._parse_json_response)
    monkeypatch.setattr(module, "get_router", lambda: router)
    monkeypatch.setattr(module, "get_primary_model_name", lambda: "synthetic-vision")
    monkeypatch.setattr(module, "_acquire_rate_limit_if_enabled", AsyncMock())
    monkeypatch.setattr(module, "_parse_json_response", parse)
    with pytest.raises(LLMServiceError, match="byte limit"):
        await module.json_completion([{"role": "user", "content": "inspect"}], max_response_bytes=24)
    assert router.acompletion.await_count == (1 if stage == "first" else 2)
    assert parse.call_count == (0 if stage == "first" else 1)
