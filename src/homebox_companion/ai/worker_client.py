"""Call the private worker without provider credentials or automatic retries."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..core import config
from .vision_contract import (
    MAX_RESULT_BYTES,
    MAX_SCHEMA_BYTES,
    VisionBackendError,
    VisionFailure,
    VisionReadiness,
    VisionRequest,
    VisionResponse,
    bounded_json,
)
from .vision_profiles import selected_profile

MAX_BODY_BYTES = 57 * 1024 * 1024
MAX_REPLY_BYTES = MAX_RESULT_BYTES + 64 * 1024


class WorkerScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    environment: Literal["dev", "qa"]
    homebox_instance: UUID
    collection_id: UUID
    draft_id: UUID
    analysis_revision: int = Field(ge=0, le=2147483647, strict=True)

    @field_validator("homebox_instance", "collection_id", "draft_id")
    @classmethod
    def nonzero(cls, value: UUID) -> UUID:
        if not value.int:
            raise ValueError("A nonzero UUID is required")
        return value


class WorkerReply(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    version: Literal["1"]
    context: WorkerScope
    vision: VisionResponse


@dataclass
class WorkerCall:
    request_id: UUID
    profile_revision: str
    deadline: float = 0
    scope: WorkerScope | None = None
    owner: str | None = None
    adapter: CodexBackend | None = None
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)


current_call: ContextVar[WorkerCall | None] = ContextVar("vision_worker_call", default=None)


def codex_schema(schema: dict | None) -> dict:
    """Close declared objects for Codex while retaining nullable field types."""
    if schema is None:
        raise VisionBackendError("invalid_input")
    result = json.loads(bounded_json(schema, MAX_SCHEMA_BYTES))

    def visit(node):
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        if node.get("type") == "object" or "properties" in node:
            if isinstance(node.get("additionalProperties"), dict):
                raise VisionBackendError("invalid_input")
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
        for key in ("properties", "$defs", "definitions"):
            for child in node.get(key, {}).values():
                visit(child)
        for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
            for child in node.get(key, []):
                visit(child)
        visit(node.get("items"))

    visit(result)
    bounded_json(result, MAX_SCHEMA_BYTES)
    return result


class CodexBackend:
    kind = "codex_cli"

    def __init__(self, response_model=None):
        self.model = selected_profile().model
        settings = config.settings
        self.url = settings.vision_worker_url.rstrip("/")
        self.token = settings.vision_worker_token
        self.environment = settings.vision_environment
        self.call: WorkerCall | None = None

    def client(self, timeout: float) -> httpx.AsyncClient:
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "host.docker.internal"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or not self.token
            or not self.token.get_secret_value().strip()
        ):
            raise VisionBackendError("not_ready")
        return httpx.AsyncClient(
            base_url=self.url,
            headers={"Authorization": "Bearer " + self.token.get_secret_value()},
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )

    async def exchange(self, method: str, route: str, timeout: float, payload=None, limit=65536):
        try:
            async with self.client(timeout) as client:
                async with client.stream(method, route, json=payload) as response:
                    if response.headers.get("content-encoding", "identity") != "identity":
                        raise VisionBackendError("invalid_response")
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if len(data) + len(chunk) > limit:
                            raise VisionBackendError("invalid_response")
                        data.extend(chunk)
                    value = json.loads(data)
                    if not isinstance(value, dict):
                        raise VisionBackendError("invalid_response")
                    if response.status_code != 200:
                        if "vision" in value:
                            failure = VisionFailure.model_validate(value["vision"])
                            call = current_call.get()
                            if call is None or failure.request_id != call.request_id or failure.backend != self.kind:
                                raise VisionBackendError("invalid_response")
                        reason = value.get("reason")
                        if reason in {"auth_required", "unsupported_version", "busy", "quota", "cancelled"}:
                            raise VisionBackendError("provider_failed", reason=reason)
                        code = (
                            reason
                            if reason in {"not_ready", "invalid_input", "invalid_response", "timeout"}
                            else "provider_failed"
                        )
                        raise VisionBackendError(code)
                    return value
        except httpx.TimeoutException:
            raise VisionBackendError("timeout") from None
        except httpx.HTTPError:
            raise VisionBackendError("not_ready") from None
        except ValueError, TypeError, RecursionError:
            raise VisionBackendError("invalid_response") from None

    async def readiness(self) -> VisionReadiness:
        value = await self.exchange("GET", "/v1/readiness/codex_cli", 15)
        if value.pop("version", None) != "1" or value.pop("environment", None) != self.environment:
            raise VisionBackendError("invalid_response")
        try:
            result = VisionReadiness.model_validate(value, strict=True)
        except ValidationError:
            raise VisionBackendError("invalid_response") from None
        if result.backend != self.kind:
            raise VisionBackendError("invalid_response")
        return result

    async def cancel(self) -> bool:
        if self.call is None or self.call.scope is None:
            return False
        payload = {**self.call.scope.model_dump(mode="json"), "request_id": str(self.call.request_id)}
        value = await self.exchange("POST", "/v1/cancel", 5, payload)
        if set(value) != {"cancelled"} or type(value["cancelled"]) is not bool:
            raise VisionBackendError("invalid_response")
        return value["cancelled"]

    async def complete(self, request: VisionRequest) -> VisionResponse:
        call = current_call.get()
        if call is None or call.scope is None or call.request_id != request.request_id:
            raise VisionBackendError("invalid_input")
        self.call = call
        call.adapter = self
        payload = {
            "version": "1",
            **call.scope.model_dump(mode="json"),
            "photo_sha256": [
                hashlib.sha256(base64.b64decode(uri.split(",", 1)[1])).hexdigest() for uri in request.image_data_uris
            ],
            "vision": request.model_copy(update={"response_schema": codex_schema(request.response_schema)}).model_dump(
                mode="json"
            ),
        }
        if len(json.dumps(payload, ensure_ascii=True).encode()) > MAX_BODY_BYTES:
            raise VisionBackendError("invalid_input")
        try:
            value = await self.exchange("POST", "/v1/vision", request.timeout_seconds, payload, MAX_REPLY_BYTES)
            reply = WorkerReply.model_validate(value)
            if reply.context != call.scope:
                raise VisionBackendError("invalid_response")
            return reply.vision
        except asyncio.CancelledError:
            try:
                async with asyncio.timeout(5):
                    await self.cancel()
            except TimeoutError, VisionBackendError:
                pass
            raise
        except ValidationError:
            raise VisionBackendError("invalid_response") from None
        finally:
            call.adapter = None
