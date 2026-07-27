"""Real FFprobe/FFmpeg coverage with a tiny generated media fixture."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.modules.media.technical import (
    FFmpegDerivativeAdapter,
    FFprobeAdapter,
    TechnicalPermanentError,
)


@pytest.mark.asyncio
async def test_real_media_fixture_is_probed_and_derivatives_are_generated(tmp_path: Path) -> None:
    source = tmp_path / "fixture.mp4"
    subprocess.run(
        [
            "ffmpeg",
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
    metadata = await FFprobeAdapter().probe(input_path=source, timeout_seconds=10)
    thumbnail, proxy = await FFmpegDerivativeAdapter().generate(
        input_path=source, output_dir=tmp_path, timeout_seconds=10
    )
    assert metadata.video_codec == "h264" and metadata.width == 64 and metadata.height == 48
    assert metadata.has_audio is False and metadata.rotation_degrees == 0
    assert thumbnail.byte_size > 0 and proxy.byte_size > 0
    assert len(thumbnail.sha256_checksum) == 64 and len(proxy.sha256_checksum) == 64


def test_probe_normalizes_display_matrix_rotation() -> None:
    metadata = FFprobeAdapter._normalize(
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
        await FFprobeAdapter().probe(input_path=invalid, timeout_seconds=10)
