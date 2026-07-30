"""Deterministically materialize the FFmpeg-producible golden clips.

The committed golden set is machine-readable ground truth only — no media bytes. This script
rebuilds the synthetic clips (testsrc/color/overlays + a silent, correctly-timed audio track)
from each sample's ``spec`` so a real-provider run has reproducible input. It never downloads
anything; real Turkish speech is operator-supplied per SOURCES.md.

Run from ``services/api``:

    python -m scripts.make_golden_media                 # dry run: print the commands
    python -m scripts.make_golden_media --execute --output-dir /tmp/golden
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from app.benchmark.golden import GoldenSample, load_samples

_SILENT_AUDIO = "anullsrc=channel_layout=mono:sample_rate=16000"


def _media_spec(sample: GoldenSample) -> Mapping[str, object] | None:
    media = sample.media
    if media is None or media.get("generator") != "ffmpeg_lavfi":
        return None
    return media


def build_ffmpeg_command(
    sample: GoldenSample, output_dir: Path, *, ffmpeg: str = "ffmpeg"
) -> list[str] | None:
    """Return a deterministic FFmpeg command for a media sample, or None if not generatable."""

    media = _media_spec(sample)
    if media is None:
        return None
    spec = media.get("spec")
    if not isinstance(spec, dict):
        return None
    video_source = spec.get("video")
    if not isinstance(video_source, str) or not video_source:
        return None
    duration_ms = media.get("duration_ms")
    duration_seconds = (duration_ms / 1000) if isinstance(duration_ms, int) else 5.0

    command = [ffmpeg, "-y", "-f", "lavfi", "-i", video_source]
    audio = spec.get("audio")
    has_audio = audio != "none"
    if has_audio:
        # A silent, correctly-timed stand-in; real speech is operator-supplied (SOURCES.md).
        command += ["-f", "lavfi", "-i", _SILENT_AUDIO]
    command += ["-t", f"{duration_seconds:g}", "-pix_fmt", "yuv420p", "-c:v", "libx264"]
    if has_audio:
        command += ["-c:a", "aac", "-shortest"]
    command.append(str(output_dir / f"{sample.id}.mp4"))
    return command


def plan_commands(
    samples: tuple[GoldenSample, ...], output_dir: Path, *, ffmpeg: str = "ffmpeg"
) -> list[tuple[str, list[str]]]:
    plans: list[tuple[str, list[str]]] = []
    for sample in samples:
        command = build_ffmpeg_command(sample, output_dir, ffmpeg=ffmpeg)
        if command is not None:
            plans.append((sample.id, command))
    return plans


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the golden media set with FFmpeg.")
    parser.add_argument("--output-dir", type=Path, default=Path("golden-media"))
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg binary (default: ffmpeg).")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run FFmpeg; without it the commands are only printed.",
    )
    parser.add_argument("--samples-dir", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    samples = load_samples(args.samples_dir)
    plans = plan_commands(samples, args.output_dir, ffmpeg=args.ffmpeg)
    if not plans:
        print("no FFmpeg-producible samples found", file=sys.stderr)
        return 0

    operator_supplied = [
        sample.id
        for sample in samples
        if isinstance(sample.media, dict)
        and isinstance(sample.media.get("operator_supplied"), dict)
    ]
    if operator_supplied:
        print(
            "operator-supplied assets required for: "
            + ", ".join(operator_supplied)
            + " (see tests/fixtures/golden/SOURCES.md)",
            file=sys.stderr,
        )

    if args.execute:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for sample_id, command in plans:
            print(f"generating {sample_id} -> {command[-1]}", file=sys.stderr)
            subprocess.run(command, check=True)  # noqa: S603 - fixed args, no shell
    else:
        for sample_id, command in plans:
            print(f"# {sample_id}\n{' '.join(command)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
