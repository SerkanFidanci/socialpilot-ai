"""The `RenderPort` boundary: adapter selection, capability honesty, and domain purity.

Acceptance criterion 8 of the work order is a structural claim — FFmpeg is *an* adapter, not
the port — and a claim like that decays unless something checks it. The source scans below are
that check: they fail the moment a provider detail leaks back across the boundary.
"""

from __future__ import annotations

import ast
import inspect
import tokenize
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.infrastructure.render import create_render
from app.infrastructure.render.fake import FakeRenderAdapter
from app.infrastructure.render.ffmpeg import FFmpegRenderAdapter, _ass_text, _overlay_position
from app.modules.content.render import AiDisclosureState, ProvenanceState, RenderProfile
from app.modules.content.render_service import ContentRenderService
from app.modules.content.service import current_disclosure_state
from app.modules.content.timeline import (
    AudioTrackKind,
    CaptionSource,
    OverlayAnchor,
    TransitionKind,
)

MODULES = Path(__file__).resolve().parents[2] / "app" / "modules"


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://test:test@localhost:5432/test",
        "redis_url": "redis://localhost:6379/0",
        "celery_broker_url": "redis://localhost:6379/1",
        "celery_result_backend": "redis://localhost:6379/2",
    }
    return Settings(**(base | overrides))  # type: ignore[arg-type]


# --- the boundary ----------------------------------------------------------------------------


def executable_source(path: Path) -> str:
    """Return a file's code with comments and string literals removed.

    Prose *about* the boundary belongs in docstrings — explaining why FFmpeg sits behind the
    port is the documentation doing its job. What must not exist is executable code that names
    a provider: an import, an attribute access, a command fragment. Tokenizing separates the
    two so the check is about coupling rather than about vocabulary.
    """

    pieces: list[str] = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type not in (tokenize.COMMENT, tokenize.STRING):
                pieces.append(token.string)
    return " ".join(pieces).lower()


def test_content_domain_has_no_executable_reference_to_a_render_provider() -> None:
    """FFmpeg is an adapter, not the port — enforced, not merely intended."""

    offenders: list[str] = []
    for path in (MODULES / "content").rglob("*.py"):
        code = executable_source(path)
        for needle in ("ffmpeg", "ffprobe", "subprocess", "libx264", "drawtext", "popen"):
            if needle in code:
                offenders.append(f"{path.name}:{needle}")
    assert offenders == [], offenders


def test_content_domain_imports_no_infrastructure(  # noqa: D103
) -> None:
    offenders: list[str] = []
    for path in (MODULES / "content").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "app.infrastructure"
            ):
                offenders.append(f"{path.name}:{node.module}")
    assert offenders == [], offenders


def test_no_binary_path_is_hard_coded_in_the_domain() -> None:
    sources = list((MODULES / "content").rglob("*.py"))
    assert sources, "content module is missing"
    assert not any("/usr/bin" in path.read_text(encoding="utf-8") for path in sources)


def test_factory_refuses_the_fake_adapter_in_production() -> None:
    with pytest.raises(ValueError, match="fake render adapter"):
        settings(
            app_env="production",
            identity_adapter="local",
            storage_adapter="s3",
            materializer_adapter="s3",
            render_adapter="fake",
            s3_endpoint_url="https://example.invalid",
            s3_bucket="bucket",
            s3_access_key_id=SecretStr("key"),
            s3_secret_access_key=SecretStr("secret"),
            database_url="postgresql+asyncpg://user:pass@db:5432/app",
        )


def test_factory_selects_ffmpeg_when_configured_and_fake_otherwise() -> None:
    assert isinstance(create_render(settings(render_adapter="ffmpeg")), FFmpegRenderAdapter)
    assert isinstance(create_render(settings(render_adapter="fake")), FakeRenderAdapter)


def test_fake_and_real_adapters_declare_the_same_capabilities() -> None:
    """A timeline the fake accepts must be one the real adapter would also accept."""

    real = FFmpegRenderAdapter(settings(render_adapter="ffmpeg")).capabilities
    fake = FakeRenderAdapter().capabilities
    assert (real.crop_modes, real.transitions, real.audio_sources, real.caption_sources) == (
        fake.crop_modes,
        fake.transitions,
        fake.audio_sources,
        fake.caption_sources,
    )
    assert real.max_video_tracks == fake.max_video_tracks


def test_capabilities_exclude_what_this_slice_cannot_do() -> None:
    capabilities = FFmpegRenderAdapter(settings(render_adapter="ffmpeg")).capabilities
    assert TransitionKind.FADE not in capabilities.transitions
    # Slice 2E implemented speech, so it is declared. `music` is not, and the distinction is not
    # effort: a music track needs a licence record (§18.3) before anything may lay one, and
    # declaring the source would turn that missing record into a half-finished render.
    assert AudioTrackKind.VOICEOVER in capabilities.audio_sources
    assert AudioTrackKind.MUSIC not in capabilities.audio_sources
    assert CaptionSource.VOICEOVER not in capabilities.caption_sources
    assert capabilities.supports_provenance_manifest is False
    assert set(capabilities.profiles) == set(RenderProfile)


# --- no AI in the render path ------------------------------------------------------------------


def test_the_render_service_has_no_way_to_reach_a_model() -> None:
    """Criterion 2's "no AI call" claim, enforced by the constructor's shape.

    There is no scene-detection, ASR, or vision port among the collaborators, so a render
    cannot make a provider call regardless of what any implementation later decides to do.
    """

    parameters = set(inspect.signature(ContentRenderService.__init__).parameters) - {"self"}
    assert parameters == {"session", "settings", "materializer", "render", "storage"}


def test_disclosure_is_none_because_nothing_generative_runs() -> None:
    assert current_disclosure_state() is AiDisclosureState.NONE


def test_reencoding_reports_provenance_as_pending_reattachment() -> None:
    """A stripped C2PA manifest is recorded, not silently lost."""

    assert ProvenanceState.STRIPPED_PENDING_REATTACH.value == "stripped_pending_reattach"
    assert (
        FFmpegRenderAdapter(
            settings(render_adapter="ffmpeg")
        ).capabilities.supports_provenance_manifest
        is False
    )


# --- adapter internals that carry safety weight -------------------------------------------------


def test_caption_text_cannot_carry_subtitle_markup() -> None:
    hostile = "Fiyat {\\an8}gizli\\N kaçış ığşçöü"
    cleaned = _ass_text(hostile)
    assert "{" not in cleaned and "}" not in cleaned and "\\" not in cleaned
    # Turkish glyphs survive; only the markup characters are removed.
    assert "ığşçöü" in cleaned


def test_caption_text_is_flattened_to_one_line() -> None:
    assert _ass_text("iki\nsatır  boşluk") == "iki satır boşluk"


@pytest.mark.parametrize("anchor", list(OverlayAnchor))
def test_every_anchor_produces_an_expression_inside_the_safe_box(anchor: OverlayAnchor) -> None:
    from app.infrastructure.render.ffmpeg import _Box

    box = _Box(60, 260, 1020, 1530)
    x, y = _overlay_position(anchor, box, width_expression="text_w", height_expression="text_h")
    assert str(box.x0) in x or str(box.x1) in x
    assert str(box.y0) in y or str(box.y1) in y


def test_font_path_outside_the_safe_character_set_is_refused() -> None:
    adapter = FFmpegRenderAdapter(
        settings(render_adapter="ffmpeg", render_font_file="/fonts/evil:name.ttf")
    )
    from app.infrastructure.render.ffmpeg import _Box
    from app.modules.content.render import PlannedText
    from app.modules.content.timeline import TEXT_STYLES

    text = PlannedText(
        text="x",
        style=TEXT_STYLES["brand-caption-v1"],
        anchor=OverlayAnchor.BOTTOM_CENTER,
        start_ms=0,
        end_ms=1_000,
    )
    from app.modules.content.render import profile_spec

    with pytest.raises(Exception, match="RENDER_FONT_PATH_INVALID"):
        adapter._drawtext(
            text,
            "text-000.txt",
            profile_spec(RenderProfile.INSTAGRAM_REELS_1080X1920),
            _Box(0, 0, 10, 10),
        )
