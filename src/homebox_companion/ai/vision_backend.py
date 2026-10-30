"""Select a vision adapter independently from its model and credentials."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ValidationError

from ..core import config
from ..core.exceptions import CapabilityNotSupportedError, LLMServiceError
from .vision_contract import (
    MAX_RESULT_BYTES,
    BackendKind,
    VisionBackendError,
    VisionDiagnostics,
    VisionReadiness,
    VisionRequest,
    VisionResponse,
    bounded_json,
)

if TYPE_CHECKING:
    from typing import Any


class VisionBackend(Protocol):
    """Use the same wire contract for API and host CLI adapters."""

    kind: BackendKind
    model: str | None

    async def readiness(self) -> VisionReadiness: ...

    async def complete(self, request: VisionRequest) -> VisionResponse: ...


class LiteLLMBackend:
    kind: BackendKind = "litellm"

    def __init__(self, response_model: type[BaseModel] | None = None):
        from ..core.llm_utils import resolve_llm_credentials

        self.response_model = response_model
        self.credentials = resolve_llm_credentials()
        self.model = self.credentials.model

    async def readiness(self) -> VisionReadiness:
        return VisionReadiness(backend=self.kind, ready=bool(self.model and self.credentials.api_key))

    async def complete(self, request: VisionRequest) -> VisionResponse:
        from .llm import _litellm_vision_completion

        started = asyncio.get_running_loop().time()
        data = await _litellm_vision_completion(
            request.system_prompt,
            request.user_prompt,
            request.image_data_uris,
            expected_keys=request.expected_keys or None,
            response_model=self.response_model,
            timeout=request.timeout_seconds,
        )
        return VisionResponse(
            request_id=request.request_id,
            backend=self.kind,
            data=data,
            diagnostics=VisionDiagnostics(elapsed_ms=round((asyncio.get_running_loop().time() - started) * 1000)),
        )


class UnavailableBackend:
    """Reject a selected CLI until its real worker adapter is installed."""

    model = None

    def __init__(self, kind: BackendKind):
        self.kind = kind

    async def readiness(self) -> VisionReadiness:
        return VisionReadiness(backend=self.kind, ready=False)

    async def complete(self, request: VisionRequest) -> VisionResponse:
        raise VisionBackendError("not_ready")


BACKEND_FACTORIES: dict[BackendKind, Callable[[type[BaseModel] | None], VisionBackend]] = {"litellm": LiteLLMBackend}


def get_vision_backend(response_model: type[BaseModel] | None = None) -> VisionBackend:
    """Select one explicit backend. Never fall back to a different kind."""
    kind = config.settings.vision_backend
    factory = BACKEND_FACTORIES.get(kind)
    backend = factory(response_model) if factory else UnavailableBackend(kind)
    if backend.kind != kind:
        raise VisionBackendError("not_ready")
    return backend


async def run_vision(
    request: VisionRequest, backend: VisionBackend, response_model: type[BaseModel] | None
) -> dict[str, Any]:
    """Check readiness, one deadline, response identity, and field types."""
    if request.response_schema is not None and response_model is None:
        raise VisionBackendError("invalid_input")
    clock = asyncio.get_running_loop().time
    deadline = clock() + request.timeout_seconds
    try:
        async with asyncio.timeout(request.timeout_seconds):
            readiness = await backend.readiness()
            if readiness.backend != request.backend or not readiness.ready:
                raise VisionBackendError("not_ready")
            if (
                backend.kind != request.backend
                or not readiness.vision
                or (len(request.image_data_uris) > 1 and not readiness.multi_image)
                or (request.response_schema is not None and not readiness.structured_output)
            ):
                raise VisionBackendError("not_ready")
            response = await backend.complete(request)
            if clock() >= deadline:
                raise VisionBackendError("timeout")
            if (
                response.version != request.version
                or response.request_id != request.request_id
                or response.backend != request.backend
            ):
                raise VisionBackendError("invalid_response")
            encoded = bounded_json(response.data, MAX_RESULT_BYTES)
            if any(key not in response.data for key in request.expected_keys):
                raise VisionBackendError("invalid_response")
            if response_model:
                response_model.model_validate_json(encoded, strict=True)
            if clock() >= deadline:
                raise VisionBackendError("timeout")
            return response.data
    except VisionBackendError:
        raise
    except TimeoutError:
        raise VisionBackendError("timeout") from None
    except ValidationError, ValueError, TypeError, RecursionError:
        raise VisionBackendError("invalid_response") from None
    except CapabilityNotSupportedError:
        raise
    except LLMServiceError:
        raise VisionBackendError("provider_failed") from None
    except Exception:
        raise VisionBackendError("provider_failed") from None
