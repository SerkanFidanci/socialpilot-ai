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


@pytest.mark.asyncio
async def test_real_media_fixture_is_probed_and_derivatives_are_generated(tmp_path: Path) -> None:
    resolved_settings = settings()
    source = tmp_path / "fixture.mp4"
    subprocess.run(
        [
            resolved_settings.ffmpeg_binary,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x48:r=12",
            "-t",
            "1",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    metadata = await FFprobeAdapter(resolved_settings).probe(input_path=source, timeout_seconds=10)
    thumbnail, proxy = await FFmpegDerivativeAdapter(resolved_settings).generate(
        input_path=source, output_dir=tmp_path, timeout_seconds=10
    )
    assert metadata.video_codec == "h264" and metadata.width == 64 and metadata.height == 48
    assert metadata.has_audio is False and metadata.rotation_degrees == 0
    assert thumbnail.byte_size > 0 and proxy.byte_size > 0
    assert len(thumbnail.sha256_checksum) == 64 and len(proxy.sha256_checksum) == 64


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


@pytest.mark.parametrize("width,height", [(3_841, 48), (64, 2_161), (3_000, 3_000)])
def test_excessive_dimensions_are_permanent_validation_errors(width: int, height: int) -> None:
    with pytest.raises(TechnicalPermanentError, match="TECHNICAL_DIMENSIONS_EXCEEDED"):
        validate_technical_metadata(
            settings=settings(), asset_byte_size=100, metadata=metadata(width=width, height=height)
        )


@pytest.mark.asyncio
async def test_derivative_size_limit_prevents_generated_output(tmp_path: Path) -> None:
    source = tmp_path / "fixture.mp4"
    resolved_settings = settings().model_copy(update={"media_max_derivative_bytes": 1})
    subprocess.run(
        [
            resolved_settings.ffmpeg_binary,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x48:r=12",
            "-t",
            "1",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
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
