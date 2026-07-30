"""AI capability adapters and their selection, mirroring `create_storage` / `create_render`."""

from __future__ import annotations

from typing import Final

from app.core.config import Settings
from app.infrastructure.ai.fake_script import (
    DisabledScriptGenerationAdapter,
    FakeScriptGenerationAdapter,
)
from app.modules.content.script import ScriptGenerationPort

__all__ = [
    "DisabledScriptGenerationAdapter",
    "FakeScriptGenerationAdapter",
    "create_script_generator",
]

PRODUCTION_DISABLED_REASON: Final = (
    "no production script-generation provider is configured; the fixture adapter is refused"
)
CONFIGURED_DISABLED_REASON: Final = "script generation is switched off by configuration"


def create_script_generator(settings: Settings) -> ScriptGenerationPort:
    """Build the configured script adapter; the fixture never produces real content.

    The storage, materializer and render factories all refuse `fake` in production by raising —
    but those adapters are on the path of the pipeline that already exists, so a deployment
    misconfigured that way is broken from the first request either way. This capability is
    different in one respect that changes the answer: **fixture prose is publishable.** A fake
    render writes an obviously placeholder file; a fake script writes fluent Turkish marketing
    copy that a reviewer could approve and post.

    So production gets an adapter that declines with a documented code (`503
    SCRIPT_GENERATION_NOT_CONFIGURED`) rather than an application that refuses to start. Boot is
    unaffected, every other endpoint keeps serving, and the one thing that cannot happen is
    fixture text reaching a customer. `Settings` deliberately does not add this adapter to
    `reject_non_production_adapters` for the same reason.
    """

    if settings.script_generation_adapter == "disabled":
        return DisabledScriptGenerationAdapter(reason=CONFIGURED_DISABLED_REASON)
    if settings.app_env == "production":
        return DisabledScriptGenerationAdapter(reason=PRODUCTION_DISABLED_REASON)
    return FakeScriptGenerationAdapter(settings)
