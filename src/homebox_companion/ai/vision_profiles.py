"""Keep the selected connection and API credentials stable during one request."""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING

from ..core import config, persistent_settings
from .vision_contract import BackendKind, VisionBackendError

if TYPE_CHECKING:
    from ..core.llm_utils import LLMCredentials
    from ..core.persistent_settings import ModelProfile


@dataclass(frozen=True)
class VisionProfile:
    backend: BackendKind
    model: str | None
    revision: str
    credentials: LLMCredentials = field(repr=False)
    fallback: ModelProfile | None = field(default=None, repr=False)

    @cached_property
    def router(self):
        from ..core.llm_router import _get_profile_router

        if self.backend != "litellm":
            raise VisionBackendError("not_ready")
        return _get_profile_router(self)


current_profile: ContextVar[VisionProfile | None] = ContextVar("vision_profile", default=None)


def profile_backend(profile: ModelProfile | None = None) -> BackendKind:
    if profile is None:
        profile = persistent_settings.get_primary_profile()
    return profile.backend if profile and profile.backend is not None else config.settings.vision_backend


def resolve_vision_profile() -> VisionProfile:
    from ..core.llm_utils import resolve_llm_credentials

    settings = persistent_settings.get_settings()
    primary = next((p for p in settings.llm_profiles if p.status == persistent_settings.ProfileStatus.PRIMARY), None)
    kind = primary.backend if primary and primary.backend is not None else config.settings.vision_backend
    credentials = resolve_llm_credentials(primary=primary, use_current_profile=False)
    model = (
        credentials.model
        if kind == "litellm"
        else (primary.model.strip() or None if primary and primary.backend == "codex_cli" else None)
    )
    try:
        stamp = persistent_settings.SETTINGS_FILE.stat().st_mtime_ns
    except FileNotFoundError:
        stamp = 0
    revision = hashlib.sha256(json.dumps([kind, model, primary.name if primary else None, stamp]).encode()).hexdigest()
    fallback = next((p for p in settings.llm_profiles if p.status == persistent_settings.ProfileStatus.FALLBACK), None)
    if kind != "litellm":
        fallback = None
    return VisionProfile(kind, model, revision, credentials, fallback)


def selected_profile() -> VisionProfile:
    return current_profile.get() or resolve_vision_profile()


def require_api_chat() -> None:
    """Reject direct model calls when the active connection is a CLI."""
    pinned = current_profile.get()
    if (pinned.backend if pinned else profile_backend()) != "litellm":
        raise VisionBackendError("not_ready", reason="chat_unavailable")
