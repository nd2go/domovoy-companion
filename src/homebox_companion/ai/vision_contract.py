"""Versioned input and output for replaceable vision backends."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core.exceptions import LLMServiceError

BackendKind = Literal["litellm", "codex_cli", "claude_cli"]
MAX_IMAGES = 30
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 40 * 1024 * 1024
MAX_SCHEMA_BYTES = 64 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_PROMPT_CHARS = 64 * 1024


class VisionBackendError(LLMServiceError):
    """Return a fixed error code without provider output or credentials."""

    def __init__(self, code: Literal["not_ready", "timeout", "invalid_input", "invalid_response", "provider_failed"]):
        self.code = code
        self.error_code = "VISION_" + code.upper()
        self.status_code = {"not_ready": 503, "timeout": 504, "invalid_input": 400}.get(code, 502)
        message = {
            "not_ready": "The selected vision backend is not ready. Check its configuration and login.",
            "timeout": "The vision request exceeded its time limit.",
            "invalid_input": "The vision request does not meet the input contract or limits.",
            "invalid_response": "The vision response does not match the request or schema.",
            "provider_failed": "The selected vision backend failed. No other backend was selected.",
        }[code]
        super().__init__(message, user_message=message)


def bounded_json(value: Any, limit: int) -> str:
    """Limit a JSON value before it crosses the backend boundary."""
    pending = [(value, 0)]
    elements = 0
    text_size = 0
    while pending:
        item, depth = pending.pop()
        elements += 1
        if elements > 65536 or depth > 64:
            raise ValueError("JSON structure limit exceeded")
        if isinstance(item, str):
            text_size += len(item)
            if text_size > limit:
                raise ValueError("JSON byte limit exceeded")
        elif isinstance(item, dict):
            if len(item) > 32768 or elements + len(pending) + 2 * len(item) > 65536:
                raise ValueError("JSON structure limit exceeded")
            pending.extend((part, depth + 1) for pair in item.items() for part in pair)
        elif isinstance(item, list):
            if len(item) > 65536 or elements + len(pending) + len(item) > 65536:
                raise ValueError("JSON structure limit exceeded")
            pending.extend((part, depth + 1) for part in item)
    encoder = json.JSONEncoder(allow_nan=False, separators=(",", ":"), ensure_ascii=True)
    parts: list[str] = []
    size = 0
    for part in encoder.iterencode(value):
        size += len(part)
        if size > limit:
            raise ValueError("JSON byte limit exceeded")
        parts.append(part)
    return "".join(parts)


class VisionRequest(BaseModel):
    """Carry photos and a schema without paths, commands, or credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True)
    version: Literal["1"] = "1"
    request_id: UUID = Field(default_factory=uuid4)
    backend: BackendKind
    model: str | None = Field(default=None, max_length=256)
    system_prompt: str = Field(max_length=MAX_PROMPT_CHARS, repr=False)
    user_prompt: str = Field(max_length=MAX_PROMPT_CHARS, repr=False)
    image_data_uris: list[str] = Field(min_length=1, max_length=MAX_IMAGES, repr=False)
    response_schema: dict[str, Any] | None = None
    expected_keys: list[str] = Field(default_factory=list, max_length=64)
    timeout_seconds: float = Field(default=120, gt=0, le=300)

    @field_validator("request_id")
    @classmethod
    def nonzero_id(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("A nonzero request UUID is required")
        return value

    @field_validator("response_schema")
    @classmethod
    def bounded_schema(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            bounded_json(value, MAX_SCHEMA_BYTES)
        return value

    @model_validator(mode="after")
    def bounded_images(self) -> VisionRequest:
        total = 0
        offsets = []
        for uri in self.image_data_uris:
            offset = uri.find(",")
            if (
                offset < 0
                or offset > 32
                or uri[:offset]
                not in {
                    "data:image/jpeg;base64",
                    "data:image/png;base64",
                    "data:image/webp;base64",
                    "data:image/gif;base64",
                }
            ):
                raise ValueError("Only inline base64 image data is allowed")
            length = len(uri) - offset - 1
            if not length or length % 4 or length > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
                raise ValueError("Image byte limit exceeded")
            padding = 2 if uri.endswith("==") else int(uri.endswith("="))
            predicted = length // 4 * 3 - padding
            total += predicted
            if predicted > MAX_IMAGE_BYTES or total > MAX_TOTAL_IMAGE_BYTES:
                raise ValueError("Image byte limit exceeded")
            offsets.append(offset + 1)
        for uri, offset in zip(self.image_data_uris, offsets, strict=True):
            try:
                if not base64.b64decode(uri[offset:], validate=True):
                    raise ValueError("Empty image")
            except binascii.Error, ValueError:
                raise ValueError("Invalid image encoding") from None
        return self


class VisionFailure(BaseModel):
    """Describe a wire failure without free-form provider diagnostics."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["1"] = "1"
    request_id: UUID
    backend: BackendKind
    code: Literal["not_ready", "timeout", "invalid_input", "invalid_response", "provider_failed"]


class VisionReadiness(BaseModel):
    """Describe backend readiness without a shared API-key requirement."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    backend: BackendKind
    ready: bool
    vision: bool = True
    multi_image: bool = True
    structured_output: bool = True


class VisionDiagnostics(BaseModel):
    """Keep optional execution timing separate from provider text."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    elapsed_ms: int | None = Field(default=None, ge=0, le=300000)


class VisionResponse(BaseModel):
    """Correlate structured data with one request and backend."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True)
    version: Literal["1"] = "1"
    request_id: UUID
    backend: BackendKind
    data: dict[str, Any] = Field(repr=False)
    diagnostics: VisionDiagnostics = Field(default_factory=VisionDiagnostics)

    @field_validator("data")
    @classmethod
    def bounded_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        bounded_json(value, MAX_RESULT_BYTES)
        return value
