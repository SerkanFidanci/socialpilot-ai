"""AI capability adapters and their selection, mirroring `create_storage` / `create_render`."""

from __future__ import annotations

from typing import Final

from app.core.config import Settings
from app.infrastructure.ai.audio_probe import FFprobeAudioProbe
from app.infrastructure.ai.fake_script import (
    DisabledScriptGenerationAdapter,
    FakeScriptGenerationAdapter,
)
from app.infrastructure.ai.fake_tts import DisabledTTSAdapter, FakeTTSAdapter
from app.infrastructure.ai.fake_visual_qc import (
    DisabledVisualQcAdapter,
    FakeVisualQcAdapter,
)
from app.modules.content.qc import VisualQcPort
from app.modules.content.script import ScriptGenerationPort
from app.modules.content.tts import AudioProbePort, TTSPort

__all__ = [
    "DisabledScriptGenerationAdapter",
    "DisabledTTSAdapter",
    "DisabledVisualQcAdapter",
    "FFprobeAudioProbe",
    "FakeScriptGenerationAdapter",
    "FakeTTSAdapter",
    "FakeVisualQcAdapter",
    "create_audio_probe",
    "create_script_generator",
    "create_tts",
    "create_visual_qc",
]

PRODUCTION_DISABLED_REASON: Final = (
    "no production script-generation provider is configured; the fixture adapter is refused"
)
CONFIGURED_DISABLED_REASON: Final = "script generation is switched off by configuration"
TTS_PRODUCTION_DISABLED_REASON: Final = (
    "no production text-to-speech provider is configured; the fixture adapter is refused"
)
TTS_CONFIGURED_DISABLED_REASON: Final = "text-to-speech is switched off by configuration"
VISUAL_QC_PRODUCTION_DISABLED_REASON: Final = (
    "no production vision provider is configured; the fixture adapter is refused"
)
VISUAL_QC_CONFIGURED_DISABLED_REASON: Final = (
    "visual quality control is switched off by configuration"
)


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


def create_tts(settings: Settings) -> TTSPort:
    """Build the configured speech adapter; the fixture never produces shippable audio.

    Same rule, same reason as `create_script_generator`, and it is the general one now: a
    capability whose output a human could approve and publish falls back to a `disabled` adapter
    with a documented error (`503 TTS_NOT_CONFIGURED`) instead of taking the deployment down.
    Speech qualifies twice over — it is read from copy a reviewer already approved, and an
    audience cannot tell a fixture voice from a purchased one by listening.

    `TTS_ADAPTER` is therefore absent from `reject_non_production_adapters`, exactly like
    `SCRIPT_GENERATION_ADAPTER`. The infrastructure adapters (storage, identity, materializer,
    render) keep being refused at startup, because a deployment running those as fakes is broken
    from its first request rather than quietly wrong on one endpoint.
    """

    if settings.tts_adapter == "disabled":
        return DisabledTTSAdapter(reason=TTS_CONFIGURED_DISABLED_REASON)
    if settings.app_env == "production":
        return DisabledTTSAdapter(reason=TTS_PRODUCTION_DISABLED_REASON)
    return FakeTTSAdapter(settings)


def create_visual_qc(settings: Settings) -> VisualQcPort:
    """Build the configured vision adapter; the fixture never answers a quality question for real.

    Same rule as the two factories above, and this is its sharpest case. A fixture script writes
    prose a reviewer could publish; a fixture *inspection* writes an approval a reviewer could
    act on — "no sensitive content in this frame" is a claim about a customer's video that
    nothing looked at. So production gets an adapter that declines with a documented code and the
    four model checks land as `unknown`.

    The consequence is deliberate and worth stating plainly: until a real provider is connected,
    automatic QC never returns `passed`. Every report reaches `needs_review` and asks for a
    person. That is the correct answer to "we cannot see the frames yet", and a configuration
    that returned `passed` instead would be the one bug this whole slice exists to make
    impossible.
    """

    if settings.visual_qc_adapter == "disabled":
        return DisabledVisualQcAdapter(reason=VISUAL_QC_CONFIGURED_DISABLED_REASON)
    if settings.app_env == "production":
        return DisabledVisualQcAdapter(reason=VISUAL_QC_PRODUCTION_DISABLED_REASON)
    return FakeVisualQcAdapter(settings)


def create_audio_probe(settings: Settings) -> AudioProbePort:
    """Build the audio probe. There is no fake: measurement is the guarantee.

    Every other port here has a fixture because the thing under test is the pipeline around a
    provider. The probe is the opposite — it *is* the check that a provider's account of its own
    output is not taken at face value — so a fixture probe would be a fixture verifying a
    fixture. It runs ffprobe in development and in production alike.
    """

    return FFprobeAudioProbe(settings)
