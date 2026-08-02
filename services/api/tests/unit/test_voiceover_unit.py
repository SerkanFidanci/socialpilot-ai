"""Voiceover contract, fixture adapter, ffprobe measurement and the §18.3 duration rule.

Everything here is pure or file-local: no database, no HTTP. The measurement tests do run
ffprobe, deliberately — the guarantee slice 2C makes is that a duration comes from the file
rather than from whoever produced it, and a test that mocked the probe would be asserting the
mock. They need the binary the container ships.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.infrastructure.ai import (
    DisabledTTSAdapter,
    FakeTTSAdapter,
    create_audio_probe,
    create_tts,
)
from app.infrastructure.ai.audio_probe import FFprobeAudioProbe
from app.infrastructure.ai.fake_tts import fixture_duration_ms
from app.infrastructure.render.fake import FakeRenderAdapter
from app.modules.content.render import RenderCapabilities, RenderProfile
from app.modules.content.timeline import AudioTrackKind, parse_timeline
from app.modules.content.tts import (
    MAX_VOICEOVER_LINES,
    VOICE_PROFILES,
    AudioFormat,
    AudioProbePermanentError,
    SynthesisRequest,
    TTSDisabledError,
    VoiceoverSegment,
    VoiceoverSourceError,
    resolve_voice_profile,
    script_lines,
    segment_object_key,
    total_drift_ms,
    total_duration_ms,
)
from app.modules.content.tts_service import VoiceoverRequest
from app.modules.content.validation import (
    AssetFacts,
    ValidationContext,
    VoiceoverFacts,
    validate_timeline,
)
from app.modules.operations.service import request_fingerprint

ASSET = UUID("11111111-1111-4111-8111-111111111111")
VOICEOVER = UUID("55555555-5555-4555-8555-555555555555")
PROFILE = RenderProfile.INSTAGRAM_REELS_1080X1920
FAKE_CAPABILITIES = FakeRenderAdapter().capabilities
# What an adapter that *can* mix speech would declare. Validation is a pure function over the
# capabilities it is handed, so the duration rule is testable today even though no shipped
# adapter mixes a voiceover track yet.
SPEECH_CAPABILITIES = RenderCapabilities(
    profiles=FAKE_CAPABILITIES.profiles,
    crop_modes=FAKE_CAPABILITIES.crop_modes,
    transitions=FAKE_CAPABILITIES.transitions,
    audio_sources=frozenset({AudioTrackKind.ORIGINAL, AudioTrackKind.VOICEOVER}),
    caption_sources=FAKE_CAPABILITIES.caption_sources,
    max_duration_ms=FAKE_CAPABILITIES.max_duration_ms,
    max_video_tracks=FAKE_CAPABILITIES.max_video_tracks,
    supports_provenance_manifest=False,
)


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://test:test@localhost:5432/test",
        "redis_url": "redis://localhost:6379/0",
        "celery_broker_url": "redis://localhost:6379/1",
        "celery_result_backend": "redis://localhost:6379/2",
    }
    return Settings(**(base | overrides))


def production_settings(**overrides: Any) -> Settings:
    """A production environment, assembled the only way it can be today.

    `identity_adapter` has one value and that value is refused in production, so a production
    `Settings` cannot be constructed through validation at all yet. Flipping the field afterwards
    is what lets these tests exercise the production branch instead of skipping it — the same
    device W13's suite uses for the script adapter.
    """

    configured = settings(**overrides)
    configured.app_env = "production"
    return configured


def resolved_script(**overrides: Any) -> dict[str, Any]:
    """A stored `content_scripts.document`: §18.1's contract with slots already substituted."""

    base: dict[str, Any] = {
        "hook": {"text": "Günün en taze molası hazır.", "duration_ms": 2_500},
        "segments": [
            {
                "purpose": "hook",
                "voice_text": "Günün en taze molası hazır.",
                "required_scene_tags": ["product_closeup"],
                "target_duration_ms": 2_500,
            },
            {
                "purpose": "offer",
                "voice_text": "Şimdi 149,90 TRY.",
                "required_scene_tags": ["product_closeup"],
                "target_duration_ms": 4_000,
            },
        ],
        "cta": {"text": "Bugün bizi ziyaret et.", "source": "approved_cta"},
    }
    return base | overrides


# --- what gets spoken --------------------------------------------------------------------


def test_lines_come_from_the_resolved_document_so_a_listener_hears_a_verified_value() -> None:
    """The template keeps `{{price:…}}`; the document keeps what the record said.

    Voicing the template would read a slot token aloud. Voicing the document is what makes the
    figure a listener hears one that `product_prices` actually held.
    """

    lines = script_lines(resolved_script())

    assert [line.purpose for line in lines] == ["hook", "offer"]
    assert lines[1].text == "Şimdi 149,90 TRY."
    assert lines[1].target_duration_ms == 4_000
    assert [line.index for line in lines] == [0, 1]


@pytest.mark.parametrize(
    ("document", "code"),
    [
        (None, "VOICEOVER_SCRIPT_DOCUMENT_MISSING"),
        ({"segments": []}, "VOICEOVER_SCRIPT_SEGMENTS_INVALID"),
        (
            resolved_script(
                segments=[
                    {"purpose": "hook", "voice_text": "   ", "target_duration_ms": 2_000},
                ]
            ),
            "VOICEOVER_SCRIPT_TEXT_INVALID",
        ),
        (
            resolved_script(
                segments=[
                    {"purpose": "hook", "voice_text": "Merhaba\x07", "target_duration_ms": 2_000},
                ]
            ),
            "VOICEOVER_SCRIPT_TEXT_INVALID",
        ),
        (
            resolved_script(
                segments=[
                    {"purpose": "hook", "voice_text": "Merhaba", "target_duration_ms": 0},
                ]
            ),
            "VOICEOVER_SCRIPT_SEGMENTS_INVALID",
        ),
    ],
)
def test_an_unvoiceable_document_is_a_documented_rejection(document: Any, code: str) -> None:
    with pytest.raises(VoiceoverSourceError) as error:
        script_lines(document)

    assert error.value.code == code


def test_a_document_with_more_lines_than_the_ceiling_is_refused_before_any_call() -> None:
    """The bill scales with the line count, so the ceiling is restated here rather than
    inherited from §18.1's parser by coincidence."""

    oversized = resolved_script(
        segments=[
            {"purpose": "product", "voice_text": f"Satır {index}.", "target_duration_ms": 1_000}
            for index in range(MAX_VOICEOVER_LINES + 1)
        ]
    )

    with pytest.raises(VoiceoverSourceError) as error:
        script_lines(oversized)

    assert error.value.code == "VOICEOVER_SCRIPT_TOO_MANY_LINES"


# --- the voice ---------------------------------------------------------------------------


def test_every_registered_voice_is_versioned_and_reproducible() -> None:
    """§17.6's pattern: audio whose voice cannot be named later cannot be defended."""

    assert VOICE_PROFILES, "the registry cannot be empty"
    for code, profile in VOICE_PROFILES.items():
        assert profile.code == code
        assert profile.version >= 1
        assert profile.speaking_rate > 0
        document = profile.as_document()
        assert set(document) == {
            "code",
            "version",
            "language",
            "voice",
            "style",
            "speaking_rate",
        }
        # The stored snapshot has to survive a JSONB round trip unchanged.
        assert json.loads(json.dumps(document)) == document
    assert resolve_voice_profile("nope") is None


# --- measurement -------------------------------------------------------------------------


async def test_the_fixture_writes_real_audio_that_ffprobe_can_measure(tmp_path: Path) -> None:
    """A fake that produced no bytes would make every alignment test assert its own arithmetic."""

    adapter = FakeTTSAdapter(settings())
    profile = VOICE_PROFILES["tr-warm-v1"]
    destination = tmp_path / "line.wav"

    result = await adapter.synthesize(
        request=SynthesisRequest(
            text="Günün en taze molası hazır ve seni bekliyor.",
            voice_profile=profile,
            output_format=AudioFormat.WAV,
            destination=destination,
            max_output_bytes=20_971_520,
        ),
        timeout_seconds=10,
    )
    measured = await FFprobeAudioProbe(settings()).measure(path=destination, timeout_seconds=30)

    assert destination.exists() and result.byte_size == destination.stat().st_size
    assert result.content_type == "audio/wav"
    expected = fixture_duration_ms("Günün en taze molası hazır ve seni bekliyor.", profile)
    # Rounding through the container's frame count costs a few milliseconds either way.
    assert abs(measured.duration_ms - expected) <= 20
    assert measured.channels == 1 and measured.sample_rate_hz == 22_050


async def test_the_probe_refuses_a_file_that_is_not_audio(tmp_path: Path) -> None:
    not_audio = tmp_path / "note.txt"
    not_audio.write_text("bu bir ses dosyası değil", encoding="utf-8")

    with pytest.raises(AudioProbePermanentError):
        await FFprobeAudioProbe(settings()).measure(path=not_audio, timeout_seconds=30)


async def test_the_measurement_is_independent_of_what_the_provider_claims(
    tmp_path: Path,
) -> None:
    """The whole reason the probe exists: a provider's account of its own output is a claim."""

    adapter = FakeTTSAdapter(settings(), declared_duration_ms=999_000)
    destination = tmp_path / "line.wav"

    result = await adapter.synthesize(
        request=SynthesisRequest(
            text="Kısa bir cümle.",
            voice_profile=VOICE_PROFILES["tr-warm-v1"],
            output_format=AudioFormat.WAV,
            destination=destination,
            max_output_bytes=20_971_520,
        ),
        timeout_seconds=10,
    )
    measured = await create_audio_probe(settings()).measure(path=destination, timeout_seconds=30)

    assert result.declared_duration_ms == 999_000
    assert measured.duration_ms < 5_000


# --- production behaviour (acceptance criterion 5) ----------------------------------------


def test_production_gets_the_disabled_adapter_instead_of_the_fixture() -> None:
    assert isinstance(create_tts(production_settings()), DisabledTTSAdapter)
    assert isinstance(create_tts(settings(tts_adapter="disabled")), DisabledTTSAdapter)
    assert isinstance(create_tts(settings()), FakeTTSAdapter)


def test_production_boot_is_not_refused_over_the_tts_adapter() -> None:
    """`TTS_ADAPTER=fake` in production must not take the deployment down.

    The rule W13 settled and PM generalized. The startup gate names every development-only
    adapter it refuses; the speech adapter has to be absent from that list, because it is
    handled by the factory rather than by refusing to boot.
    """

    with pytest.raises(ValidationError) as error:
        settings(
            app_env="production",
            identity_adapter="local",
            storage_adapter="s3",
            materializer_adapter="s3",
            render_adapter="ffmpeg",
            tts_adapter="fake",
            script_generation_adapter="fake",
            s3_endpoint_url="https://example.invalid",
            s3_bucket="bucket",
            s3_access_key_id="key",
            s3_secret_access_key="secret",
            database_url="postgresql+asyncpg://user:pass@db:5432/app",
        )

    message = str(error.value)
    assert "identity" in message
    assert "tts" not in message and "speech" not in message


def test_the_fixture_adapter_cannot_be_constructed_in_production() -> None:
    with pytest.raises(RuntimeError, match="TTS_FAKE_ADAPTER_NOT_ALLOWED_IN_PRODUCTION"):
        FakeTTSAdapter(production_settings())


async def test_the_disabled_adapter_declines_every_call(tmp_path: Path) -> None:
    adapter = DisabledTTSAdapter(reason="no provider")

    assert adapter.descriptor.enabled is False
    with pytest.raises(TTSDisabledError):
        await adapter.synthesize(
            request=SynthesisRequest(
                text="Merhaba.",
                voice_profile=VOICE_PROFILES["tr-warm-v1"],
                output_format=AudioFormat.WAV,
                destination=tmp_path / "x.wav",
                max_output_bytes=1_024,
            ),
            timeout_seconds=10,
        )


def test_the_run_timeout_must_cover_one_call_and_its_measurement() -> None:
    with pytest.raises(ValidationError, match="TTS_TOTAL_TIMEOUT_SECONDS"):
        settings(tts_total_timeout_seconds=30, tts_timeout_seconds=60, tts_probe_timeout_seconds=30)


# --- alignment (acceptance criterion 6) ---------------------------------------------------


def timeline_document(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": "1.0",
        "canvas": {"width": 1080, "height": 1920, "fps": 30, "duration_ms": 6_000},
        "video_tracks": [
            {
                "track": 1,
                "clips": [
                    {
                        "asset_id": str(ASSET),
                        "source_start_ms": 0,
                        "source_end_ms": 6_000,
                        "timeline_start_ms": 0,
                        "crop_mode": "smart_cover",
                        "transition_out": "cut",
                    }
                ],
            }
        ],
        "audio_tracks": [
            {
                "type": "voiceover",
                "asset_id": str(VOICEOVER),
                "gain_db": 0,
                "duck_under_voice": False,
            }
        ],
        "overlays": [],
        "captions": {"enabled": False, "source": "transcript", "style_id": "brand-caption-v1"},
    }
    return base | overrides


def context(**overrides: Any) -> ValidationContext:
    defaults: dict[str, Any] = {
        "assets": {
            ASSET: AssetFacts(
                asset_id=ASSET,
                duration_ms=10_000,
                width=1080,
                height=1920,
                has_audio=True,
                renderable=True,
                source_object_key="tenant/a/media/a/original",
            )
        },
        "logo_asset_ids": frozenset(),
        "forbidden_terms": (),
        "verified_values": {},
        "now": datetime(2026, 8, 1, tzinfo=UTC),
        "voiceovers": {
            VOICEOVER: VoiceoverFacts(voiceover_id=VOICEOVER, usable=True, duration_ms=5_500)
        },
    }
    return ValidationContext(**(defaults | overrides))


def check(document: dict[str, Any], **overrides: Any) -> tuple[str, ...]:
    return validate_timeline(
        parse_timeline(document),
        context=context(**overrides),
        capabilities=SPEECH_CAPABILITIES,
        profile=PROFILE,
        min_resolution_ratio=0.5,
    ).codes


def test_a_voiceover_that_fits_the_canvas_passes() -> None:
    assert check(timeline_document()) == ()


def test_speech_may_not_outlast_the_canvas_it_is_laid_over() -> None:
    """§18.3's "seslendirme süresi", bound to a real ffprobe measurement.

    Before slice 2C there was no measured duration to compare, so the rule could not exist
    honestly. The failure it now prevents is an output whose last sentence is cut off.
    """

    codes = check(
        timeline_document(),
        voiceovers={
            VOICEOVER: VoiceoverFacts(voiceover_id=VOICEOVER, usable=True, duration_ms=6_001)
        },
    )

    assert codes == ("TIMELINE_VOICEOVER_DURATION_OVERFLOW",)


def test_another_tenants_voiceover_is_simply_not_there() -> None:
    """The query is tenant-scoped, so absence covers "does not exist" and "not yours" at once."""

    assert check(timeline_document(), voiceovers={}) == ("TIMELINE_VOICEOVER_NOT_ACCESSIBLE",)


@pytest.mark.parametrize(
    "facts",
    [
        VoiceoverFacts(voiceover_id=VOICEOVER, usable=False, duration_ms=3_000),
        VoiceoverFacts(voiceover_id=VOICEOVER, usable=True, duration_ms=None),
    ],
)
def test_an_unsettled_or_unmeasured_voiceover_cannot_be_placed(facts: VoiceoverFacts) -> None:
    assert check(timeline_document(), voiceovers={VOICEOVER: facts}) == (
        "TIMELINE_VOICEOVER_NOT_READY",
    )


def test_a_media_asset_id_does_not_resolve_as_a_voiceover() -> None:
    """A `voiceover` track names a `voiceover_assets` row. Pointing it at an uploaded asset is
    not a near miss that quietly works — the two live in different tables."""

    document = timeline_document(
        audio_tracks=[
            {
                "type": "voiceover",
                "asset_id": str(ASSET),
                "gain_db": 0,
                "duck_under_voice": False,
            }
        ]
    )

    assert check(document) == ("TIMELINE_VOICEOVER_NOT_ACCESSIBLE",)


def test_the_duration_rule_is_the_only_thing_left_refusing_speech_that_does_not_fit() -> None:
    """Slice 2C wrote this rule where no adapter could mix speech; slice 2E built the mixer.

    The point W15 made was that the rule must not be one that "starts existing the day some
    adapter grows a feature" — so it was written and tested against a capability set that refused
    the track outright, and both codes came back. Now the adapters declare `voiceover`, the
    capability complaint is gone, and the duration rule is the only thing standing between speech
    and a video whose last sentence is cut off. That it still fires alone is the whole assertion.
    """

    codes = validate_timeline(
        parse_timeline(timeline_document()),
        context=context(
            voiceovers={
                VOICEOVER: VoiceoverFacts(voiceover_id=VOICEOVER, usable=True, duration_ms=6_001)
            }
        ),
        capabilities=FAKE_CAPABILITIES,
        profile=PROFILE,
        min_resolution_ratio=0.5,
    ).codes

    assert set(codes) == {"TIMELINE_VOICEOVER_DURATION_OVERFLOW"}


def test_a_voiceover_is_not_a_source_the_worker_would_try_to_materialize() -> None:
    """`asset_ids` feeds the render worker's download loop; a voiceover id in there would send
    it looking for an uploaded video that never existed."""

    timeline = parse_timeline(timeline_document())

    assert timeline.asset_ids == (ASSET,)
    assert timeline.voiceover_ids == (VOICEOVER,)


# --- records ------------------------------------------------------------------------------


def segment(**overrides: Any) -> VoiceoverSegment:
    base: dict[str, Any] = {
        "index": 0,
        "purpose": "hook",
        "object_key": "tenant/a/voiceovers/v/segment-000.wav",
        "content_type": "audio/wav",
        "byte_size": 1_024,
        "sha256_checksum": "a" * 64,
        "duration_ms": 2_800,
        "declared_duration_ms": 2_500,
        "target_duration_ms": 2_500,
    }
    return VoiceoverSegment(**(base | overrides))


def test_drift_is_measured_against_the_scripts_target_and_never_judged() -> None:
    """Slice 2D owns the threshold; this slice owns the number. Both signs are meaningful."""

    segments = (segment(), segment(index=1, duration_ms=3_500, target_duration_ms=4_000))

    assert segments[0].drift_ms == 300
    assert segments[1].drift_ms == -500
    assert total_duration_ms(segments) == 6_300
    assert total_drift_ms(segments) == -200


def test_the_stored_segment_keeps_the_claim_beside_the_measurement() -> None:
    document = segment().as_document()

    assert document["duration_ms"] == 2_800
    assert document["declared_duration_ms"] == 2_500
    assert document["drift_ms"] == 300
    assert json.loads(json.dumps(document)) == document


def test_object_keys_are_tenant_prefixed_and_ordered() -> None:
    business, voiceover = uuid4(), uuid4()

    key = segment_object_key(business, voiceover, 2, suffix=".wav")

    assert key == f"tenant/{business}/voiceovers/{voiceover}/segment-002.wav"


def test_the_idempotency_fingerprint_is_taken_over_the_whole_request() -> None:
    """W14's lesson, in a new place: a fingerprint over a summary replays the wrong answer."""

    script = uuid4()
    warm = VoiceoverRequest(script_id=script, voice_profile_code="tr-warm-v1")
    neutral = VoiceoverRequest(script_id=script, voice_profile_code="tr-neutral-v1")

    assert request_fingerprint(warm.as_payload()) != request_fingerprint(neutral.as_payload())
    assert request_fingerprint(warm.as_payload()) == request_fingerprint(
        VoiceoverRequest(script_id=script, voice_profile_code="tr-warm-v1").as_payload()
    )
