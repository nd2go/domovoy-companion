"""Bound one CLI request before multipart parsing and track its cancellation."""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from homebox_companion.ai.vision_contract import VisionBackendError
from homebox_companion.ai.vision_profiles import current_profile, resolve_vision_profile
from homebox_companion.ai.worker_client import WorkerCall, WorkerScope, current_call
from homebox_companion.core import config


def canonical_uuid(value: str | None) -> UUID:
    try:
        parsed = UUID(value or "")
        if not parsed.int or str(parsed) != value:
            raise ValueError
        return parsed
    except ValueError, TypeError, AttributeError:
        raise VisionBackendError("invalid_input") from None


async def bind_worker_context(request: Request, gateway) -> None:
    """Verify the collection with Homebox before any model call."""
    call = current_call.get()
    if call is None:
        return
    from .api.inventory_schema import homebox

    group = await homebox(gateway, "/groups")
    expected = gateway.access.group_id
    actual = group.get("id") if isinstance(group, dict) else None
    if not actual or (expected and actual != expected):
        raise HTTPException(403, "Homebox collection does not match the request")
    try:
        revision = request.headers.get("X-Companion-Analysis-Revision", "")
        if not revision.isascii() or not revision.isdigit() or len(revision) > 10:
            raise ValueError
        call.scope = WorkerScope(
            environment=config.settings.vision_environment,
            homebox_instance=canonical_uuid(config.settings.vision_homebox_instance),
            collection_id=canonical_uuid(actual),
            draft_id=canonical_uuid(request.headers.get("X-Companion-Draft-ID")),
            analysis_revision=int(revision),
        )
        call.owner = gateway.access.identity_scope
    except ValueError, ValidationError:
        raise VisionBackendError("invalid_input") from None


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    request_id: UUID
    draft_id: UUID
    analysis_revision: int = Field(ge=0, le=2147483647, strict=True)


class VisionAdmissionMiddleware:
    def __init__(self, app, settings):
        self.app = app
        self.settings = settings
        self.active: WorkerCall | None = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") not in {
            "/api/tools/vision/detect",
            "/api/tools/vision/analyze",
            "/api/tools/vision/correct",
        }:
            await self.app(scope, receive, send)
            return
        profile = resolve_vision_profile()
        if profile.backend != "codex_cli":
            token = current_profile.set(profile)
            try:
                await self.app(scope, receive, send)
            finally:
                current_profile.reset(token)
            return
        try:
            headers = Headers(scope=scope)
            if headers.get("X-Companion-Vision-Profile", profile.revision) != profile.revision:
                raise VisionBackendError("not_ready", reason="profile_changed")
            request_id = canonical_uuid(headers.get("X-Request-ID"))
            if self.active is not None:
                raise VisionBackendError("not_ready", reason="busy")
        except VisionBackendError as error:
            await JSONResponse({"detail": error.user_message, "code": error.error_code}, status_code=error.status_code)(
                scope, receive, send
            )
            return
        budget = min(300, max(1, self.settings.llm_timeout))
        call = WorkerCall(request_id, profile.revision, asyncio.get_running_loop().time() + budget)
        self.active = call
        scope["app"].state.vision_active = call
        token = current_profile.set(profile)
        call_token = current_call.set(call)
        body_done = asyncio.Event()
        sent = False
        deadline = None

        async def tracked_receive():
            message = await receive()
            if message["type"] == "http.request" and not message.get("more_body", False):
                body_done.set()
                if deadline is not None:
                    deadline.reschedule(call.deadline)
            return message

        async def tracked_send(message):
            nonlocal sent
            if message["type"] == "http.response.start":
                sent = True
            await send(message)

        async def disconnected():
            await body_done.wait()
            while True:
                if (await receive())["type"] == "http.disconnect":
                    return

        app_task = asyncio.create_task(self.app(scope, tracked_receive, tracked_send))
        disconnect_task = asyncio.create_task(disconnected())
        cancel_task = asyncio.create_task(call.cancelled.wait())
        try:
            async with asyncio.timeout(10) as deadline:
                done, _ = await asyncio.wait(
                    {app_task, disconnect_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if app_task in done or sent:
                    await app_task
                else:
                    raise VisionBackendError("provider_failed", reason="cancelled")
        except TimeoutError:
            if not sent:
                error = VisionBackendError("timeout")
                await JSONResponse({"detail": error.user_message, "code": error.error_code}, status_code=504)(
                    scope, receive, send
                )
        except VisionBackendError as error:
            if not sent and not disconnect_task.done():
                await JSONResponse(
                    {"detail": error.user_message, "code": error.error_code}, status_code=error.status_code
                )(scope, receive, send)
        finally:
            for task in (app_task, disconnect_task, cancel_task):
                task.cancel()
            await asyncio.gather(app_task, disconnect_task, cancel_task, return_exceptions=True)
            self.active = None
            scope["app"].state.vision_active = None
            current_call.reset(call_token)
            current_profile.reset(token)
