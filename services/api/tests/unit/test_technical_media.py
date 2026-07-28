"""Real FFprobe/FFmpeg coverage with a tiny generated media fixture."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.core.config import Settings
from app.infrastructure.storage.fake import FakeMultipartStorage
from app.modules.media.technical import (
    FFmpegDerivativeAdapter,
    FFprobeAdapter,
    NormalizedTechnicalMetadata,
    TechnicalPermanentError,
    validate_technical_metadata,
)


def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
    )


def metadata(*, width: int = 64, height: int = 48) -> NormalizedTechnicalMetadata:
    return NormalizedTechnicalMetadata(
        container_format="mp4",
        duration_ms=1_000,
        file_size=100,
        video_codec="h264",
        width=width,
        height=height,
        display_aspect_ratio="4:3",
        frame_rate_numerator=24,
        frame_rate_denominator=1,
        bit_rate=None,
        rotation_degrees=0,
        has_audio=False,
        audio_codec=None,
        audio_sample_rate=None,
        audio_channel_count=None,
        stream_count=1,
    )


def create_video_fixture(
    *, resolved_settings: Settings, target: Path, width: int, height: int
) -> None:
    subprocess.run(
        [
            resolved_settings.ffmpeg_binary,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={width}x{height}:r=6",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(target),
        ],
        check=True,
        capture_output=True,
    )


def probe_dimensions(*, resolved_settings: Settings, media_path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            resolved_settings.ffprobe_binary,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(media_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    width, height = result.stdout.strip().split("x", maxsplit=1)
    return int(width), int(height)


@pytest.mark.asyncio
async def test_real_vertical_media_fixture_is_probed_and_derivatives_are_generated(
    tmp_path: Path,
) -> None:
    resolved_settings = settings()
    source = tmp_path / "fixture.mp4"
    create_video_fixture(
        resolved_settings=resolved_settings, target=source, width=1_080, height=1_920
    )
    source_metadata = await FFprobeAdapter(resolved_settings).probe(
        input_path=source, timeout_seconds=10
    )
    assert source_metadata.width is not None and source_metadata.height is not None
    thumbnail, proxy = await FFmpegDerivativeAdapter(resolved_settings).generate(
        input_path=source, output_dir=tmp_path, timeout_seconds=10
    )
    thumbnail_width, thumbnail_height = probe_dimensions(
        resolved_settings=resolved_settings, media_path=thumbnail.path
    )
    proxy_metadata = await FFprobeAdapter(resolved_settings).probe(
        input_path=proxy.path, timeout_seconds=10
    )
    assert source_metadata.video_codec == "h264"
    assert (source_metadata.width, source_metadata.height) == (1_080, 1_920)
    assert source_metadata.has_audio is False and source_metadata.rotation_degrees == 0
    assert thumbnail.byte_size > 0 and proxy.byte_size > 0
    assert len(thumbnail.sha256_checksum) == 64 and len(proxy.sha256_checksum) == 64
    assert thumbnail_width <= resolved_settings.media_thumbnail_max_long_edge
    assert thumbnail_height <= resolved_settings.media_thumbnail_max_short_edge
    assert proxy_metadata.width is not None and proxy_metadata.height is not None
    assert proxy_metadata.width <= resolved_settings.media_proxy_max_long_edge
    assert proxy_metadata.height <= resolved_settings.media_proxy_max_short_edge
    assert proxy_metadata.width <= source_metadata.width
    assert proxy_metadata.height <= source_metadata.height
    assert proxy_metadata.height == 720 and proxy_metadata.width in {404, 406}
    assert proxy_metadata.width % 2 == 0 and proxy_metadata.height % 2 == 0


@pytest.mark.asyncio
async def test_horizontal_proxy_is_bounded_and_never_upscaled(tmp_path: Path) -> None:
    resolved_settings = settings()
    source = tmp_path / "horizontal.mp4"
    output_dir = tmp_path / "derivatives"
    output_dir.mkdir()
    create_video_fixture(
        resolved_settings=resolved_settings, target=source, width=1_280, height=720
    )
    source_metadata = await FFprobeAdapter(resolved_settings).probe(
        input_path=source, timeout_seconds=10
    )
    assert source_metadata.width is not None and source_metadata.height is not None
    _, proxy = await FFmpegDerivativeAdapter(resolved_settings).generate(
        input_path=source, output_dir=output_dir, timeout_seconds=10
    )
    proxy_metadata = await FFprobeAdapter(resolved_settings).probe(
        input_path=proxy.path, timeout_seconds=10
    )

    assert proxy_metadata.width is not None and proxy_metadata.height is not None
    assert (proxy_metadata.width, proxy_metadata.height) == (1_280, 720)
    assert proxy_metadata.width <= source_metadata.width
    assert proxy_metadata.height <= source_metadata.height


def test_probe_normalizes_display_matrix_rotation() -> None:
    metadata = FFprobeAdapter(settings())._normalize(
        {
            "format": {"duration": "1.0", "size": "100", "format_name": "mov,mp4"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 64,
                    "height": 48,
                    "r_frame_rate": "24/1",
                    "side_data_list": [{"rotation": "450"}],
                }
            ],
        }
    )

    assert metadata.rotation_degrees == 90


@pytest.mark.asyncio
async def test_invalid_ffprobe_input_is_a_safe_permanent_error(tmp_path: Path) -> None:
    invalid = tmp_path / "not-media;$(ignored).mp4"
    invalid.write_bytes(b"not a media file")
    with pytest.raises(TechnicalPermanentError, match="FFPROBE_INVALID_OUTPUT"):
        await FFprobeAdapter(settings()).probe(input_path=invalid, timeout_seconds=10)


@pytest.mark.parametrize(
    "width,height",
    [(1_920, 1_080), (1_080, 1_920), (3_840, 2_160), (2_160, 3_840)],
)
def test_orientation_independent_dimensions_are_accepted(width: int, height: int) -> None:
    validate_technical_metadata(
        settings=settings(), asset_byte_size=100, metadata=metadata(width=width, height=height)
    )


@pytest.mark.parametrize("width,height", [(3_841, 2_160), (2_161, 2_161)])
def test_excessive_edge_dimensions_are_permanent_validation_errors(width: int, height: int) -> None:
    with pytest.raises(TechnicalPermanentError, match="TECHNICAL_DIMENSIONS_EXCEEDED"):
        validate_technical_metadata(
            settings=settings(), asset_byte_size=100, metadata=metadata(width=width, height=height)
        )


def test_excessive_total_pixels_are_a_permanent_validation_error() -> None:
    with pytest.raises(TechnicalPermanentError, match="TECHNICAL_DIMENSIONS_EXCEEDED"):
        validate_technical_metadata(
            settings=settings().model_copy(update={"media_max_total_pixels": 2_000_000}),
            asset_byte_size=100,
            metadata=metadata(width=1_920, height=1_080),
        )


def test_legacy_dimension_environment_values_map_to_orientation_independent_limits() -> None:
    resolved_settings = Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
        media_max_width=2_160,
        media_max_height=3_840,
    )

    assert resolved_settings.media_max_long_edge == 3_840
    assert resolved_settings.media_max_short_edge == 2_160


@pytest.mark.asyncio
async def test_derivative_size_limit_prevents_generated_output(tmp_path: Path) -> None:
    source = tmp_path / "fixture.mp4"
    resolved_settings = settings().model_copy(update={"media_max_derivative_bytes": 1})
    create_video_fixture(resolved_settings=resolved_settings, target=source, width=64, height=48)
    with pytest.raises(TechnicalPermanentError, match="DERIVATIVE_SIZE_EXCEEDED"):
        await FFmpegDerivativeAdapter(resolved_settings).generate(
            input_path=source, output_dir=tmp_path, timeout_seconds=10
        )


@pytest.mark.asyncio
async def test_fake_storage_persists_file_and_returns_metadata(tmp_path: Path) -> None:
    source = tmp_path / "derivative.bin"
    source.write_bytes(b"verified derivative")
    storage = FakeMultipartStorage()
    metadata = await storage.persist_file(
        object_key="tenant/test/media/test/derivatives/thumbnail",
        source_path=source,
        content_type="image/jpeg",
    )

    assert metadata == await storage.get_object_metadata(
        object_key="tenant/test/media/test/derivatives/thumbnail"
    )
    assert (
        storage.persisted_file_for_testing(
            "tenant/test/media/test/derivatives/thumbnail"
        ).read_bytes()
        == source.read_bytes()
    )
