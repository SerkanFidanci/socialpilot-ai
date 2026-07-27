"""Safe FFprobe/FFmpeg technical analysis contracts and worker service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.modules.media.models import (
    IngestStatus,
    MediaDerivative,
    MediaDerivativeStatus,
    MediaTechnicalAnalysis,
    MediaTechnicalMetadata,
    TechnicalAnalysisStatus,
)
from app.modules.media.repository import MediaRepository
from app.modules.operations.models import (
    BackgroundJob,
    JobAttempt,
    JobAttemptStatus,
    JobStatus,
    OutboxEvent,
    OutboxStatus,
)
from app.modules.operations.repository import OperationsRepository

FFPROBE_EXECUTABLE = "/usr/bin/ffprobe"
FFMPEG_EXECUTABLE = "/usr/bin/ffmpeg"


@dataclass(frozen=True)
class NormalizedTechnicalMetadata:
    container_format: str
    duration_ms: int
    file_size: int
    video_codec: str | None
    width: int | None
    height: int | None
    display_aspect_ratio: str | None
    frame_rate_numerator: int | None
    frame_rate_denominator: int | None
    bit_rate: int | None
    rotation_degrees: int
    has_audio: bool
    audio_codec: str | None
    audio_sample_rate: int | None
    audio_channel_count: int | None
    stream_count: int


@dataclass(frozen=True)
class GeneratedDerivative:
    kind: str
    path: Path
    content_type: str
    byte_size: int
    sha256_checksum: str


class MediaMaterializerPort(Protocol):
    async def materialize(self, *, object_key: str, workdir: Path) -> Path: ...


class MediaProbePort(Protocol):
    async def probe(
        self, *, input_path: Path, timeout_seconds: int
    ) -> NormalizedTechnicalMetadata: ...


class MediaDerivativePort(Protocol):
    async def generate(
        self, *, input_path: Path, output_dir: Path, timeout_seconds: int
    ) -> tuple[GeneratedDerivative, GeneratedDerivative]: ...


class TechnicalTransientError(RuntimeError):
    pass


class TechnicalPermanentError(RuntimeError):
    pass


class FFprobeAdapter(MediaProbePort):
    """Run a fixed ffprobe binary with bounded JSON and no shell interpolation."""

    async def probe(self, *, input_path: Path, timeout_seconds: int) -> NormalizedTechnicalMetadata:
        command = [
            FFPROBE_EXECUTABLE,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(input_path),
        ]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=False,
                cwd=input_path.parent,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise TechnicalTransientError("FFPROBE_TIMEOUT") from error
        if result.returncode != 0 or len(result.stdout) > 262_144 or len(result.stderr) > 16_384:
            raise TechnicalPermanentError("FFPROBE_INVALID_OUTPUT")
        try:
            payload = json.loads(result.stdout)
            return self._normalize(payload)
        except (TypeError, ValueError, KeyError):
            raise TechnicalPermanentError("FFPROBE_INVALID_OUTPUT") from None

    @staticmethod
    def _normalize(payload: object) -> NormalizedTechnicalMetadata:
        if not isinstance(payload, dict) or not isinstance(payload.get("format"), dict):
            raise ValueError
        format_data = payload["format"]
        streams = payload.get("streams")
        if not isinstance(streams, list) or len(streams) > 32:
            raise ValueError
        duration_ms = round(float(format_data["duration"]) * 1000)
        size = int(format_data["size"])
        if duration_ms <= 0 or size <= 0:
            raise ValueError
        video = next(
            (
                item
                for item in streams
                if isinstance(item, dict) and item.get("codec_type") == "video"
            ),
            None,
        )
        audio = next(
            (
                item
                for item in streams
                if isinstance(item, dict) and item.get("codec_type") == "audio"
            ),
            None,
        )
        frame = str(video.get("r_frame_rate", "0/1")) if video else "0/1"
        numerator, denominator = (int(value) for value in frame.split("/", 1))
        if denominator <= 0 or numerator < 0:
            raise ValueError
        tags = (video or {}).get("tags", {})
        side_data = (video or {}).get("side_data_list", [])
        rotation_value = tags.get("rotate", "0") if isinstance(tags, dict) else "0"
        if isinstance(side_data, list):
            for item in side_data:
                if isinstance(item, dict) and "rotation" in item:
                    rotation_value = item["rotation"]
                    break
        rotation = int(float(str(rotation_value))) % 360
        return NormalizedTechnicalMetadata(
            container_format=str(format_data.get("format_name", "unknown")).split(",", 1)[0],
            duration_ms=duration_ms,
            file_size=size,
            video_codec=str(video.get("codec_name")) if video else None,
            width=int(video["width"]) if video and video.get("width") else None,
            height=int(video["height"]) if video and video.get("height") else None,
            display_aspect_ratio=str(video.get("display_aspect_ratio")) if video else None,
            frame_rate_numerator=numerator if video else None,
            frame_rate_denominator=denominator if video else None,
            bit_rate=int(format_data["bit_rate"]) if format_data.get("bit_rate") else None,
            rotation_degrees=rotation,
            has_audio=audio is not None,
            audio_codec=str(audio.get("codec_name")) if audio else None,
            audio_sample_rate=int(audio["sample_rate"])
            if audio and audio.get("sample_rate")
            else None,
            audio_channel_count=int(audio["channels"]) if audio and audio.get("channels") else None,
            stream_count=len(streams),
        )


class FFmpegDerivativeAdapter(MediaDerivativePort):
    async def generate(
        self, *, input_path: Path, output_dir: Path, timeout_seconds: int
    ) -> tuple[GeneratedDerivative, GeneratedDerivative]:
        thumbnail, proxy = output_dir / "thumbnail.jpg", output_dir / "proxy.mp4"
        commands = (
            [FFMPEG_EXECUTABLE, "-y", "-i", str(input_path), "-frames:v", "1", str(thumbnail)],
            [
                FFMPEG_EXECUTABLE,
                "-y",
                "-i",
                str(input_path),
                "-vf",
                "scale=-2:360",
                "-c:v",
                "libx264",
                "-an",
                str(proxy),
            ],
        )
        for command in commands:
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    command,
                    shell=False,
                    cwd=output_dir,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise TechnicalTransientError("FFMPEG_TIMEOUT") from error
            if result.returncode != 0 or len(result.stderr) > 16_384:
                raise TechnicalPermanentError("FFMPEG_DERIVATIVE_FAILED")
        return (
            self._generated("thumbnail", thumbnail, "image/jpeg"),
            self._generated("proxy", proxy, "video/mp4"),
        )

    @staticmethod
    def _generated(kind: str, path: Path, content_type: str) -> GeneratedDerivative:
        try:
            contents = path.read_bytes()
        except OSError as error:
            raise TechnicalPermanentError("FFMPEG_DERIVATIVE_FAILED") from error
        if not contents:
            raise TechnicalPermanentError("FFMPEG_DERIVATIVE_FAILED")
        return GeneratedDerivative(
            kind=kind,
            path=path,
            content_type=content_type,
            byte_size=len(contents),
            sha256_checksum=hashlib.sha256(contents).hexdigest(),
        )


class TechnicalAnalysisService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        materializer: MediaMaterializerPort,
        probe: MediaProbePort,
        derivatives: MediaDerivativePort,
    ) -> None:
        self._session, self._settings = session, settings
        self._materializer, self._probe, self._derivatives = materializer, probe, derivatives
        self._media, self._operations = MediaRepository(session), OperationsRepository(session)

    async def claim_next(self) -> BackgroundJob | None:
        """Atomically claim a due technical job using PostgreSQL SKIP LOCKED."""

        async with self._session.begin():
            job = await self._operations.claim_next_technical_analysis_job()
            if job is None:
                return None
            job.status = JobStatus.RUNNING
            job.attempt_count += 1
            job.started_at = datetime.now(UTC)
            job.finished_at = None
            job.next_attempt_at = None
            job.last_error_code = None
            job.last_error_summary = None
            self._operations.add(
                JobAttempt(
                    job_id=job.id,
                    attempt_number=job.attempt_count,
                    status=JobAttemptStatus.STARTED,
                    correlation_id=job.correlation_id,
                )
            )
            return job

    async def process_next(self, *, workdir: Path) -> BackgroundJob | None:
        job = await self.claim_next()
        if job is None:
            return None
        with TemporaryDirectory(prefix="technical-analysis-", dir=workdir) as temporary_directory:
            return await self.process_claimed(
                business_id=job.business_id,
                job_id=job.id,
                workdir=Path(temporary_directory),
            )

    async def process_claimed(
        self, *, business_id: UUID, job_id: UUID, workdir: Path
    ) -> BackgroundJob:
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if (
                job is None
                or job.job_type != "media.technical_analysis"
                or job.status != JobStatus.RUNNING
            ):
                raise ProblemException(
                    status=404,
                    code="TENANT_RESOURCE_NOT_FOUND",
                    title="Resource not found",
                    detail="The requested resource is not available.",
                )
            asset = await self._media.get_asset(business_id, job.resource_id, lock=True)
            if asset is None or asset.ingest_status != IngestStatus.READY_FOR_ANALYSIS:
                raise ProblemException(
                    status=409,
                    code="RESOURCE_STATE_CONFLICT",
                    title="Invalid media state",
                    detail="The media is not ready for technical analysis.",
                )
            job.status, job.attempt_count, job.started_at = (
                JobStatus.RUNNING,
                job.attempt_count + 1,
                datetime.now(UTC),
            )
            analysis = await self._session.scalar(
                select(MediaTechnicalAnalysis)
                .where(
                    MediaTechnicalAnalysis.business_id == business_id,
                    MediaTechnicalAnalysis.asset_id == asset.id,
                )
                .with_for_update()
            )
            if analysis is None:
                self._session.add(
                    MediaTechnicalAnalysis(
                        business_id=business_id,
                        asset_id=asset.id,
                        status=TechnicalAnalysisStatus.RUNNING,
                    )
                )
            else:
                analysis.status = TechnicalAnalysisStatus.RUNNING
                analysis.safe_error_code = None
            object_key = asset.storage_object_key
            asset_byte_size = asset.byte_size
        try:
            input_path = await self._materializer.materialize(
                object_key=object_key, workdir=workdir
            )
            metadata = await self._probe.probe(
                input_path=input_path, timeout_seconds=job.timeout_seconds
            )
            self._validate_metadata(asset_byte_size=asset_byte_size, metadata=metadata)
            derivatives = await self._derivatives.generate(
                input_path=input_path, output_dir=workdir, timeout_seconds=job.timeout_seconds
            )
        except TechnicalTransientError as error:
            return await self._fail(business_id, job_id, str(error), transient=True)
        except TechnicalPermanentError as error:
            return await self._fail(business_id, job_id, str(error), transient=False)
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            asset = (
                await self._media.get_asset(business_id, job.resource_id, lock=True)
                if job
                else None
            )
            if job is None or asset is None:
                raise RuntimeError("technical analysis resource disappeared")
            completed_analysis = await self._session.scalar(
                select(MediaTechnicalAnalysis)
                .where(MediaTechnicalAnalysis.asset_id == asset.id)
                .with_for_update()
            )
            if completed_analysis is None:
                raise RuntimeError("technical analysis state disappeared")
            completed_analysis.status, completed_analysis.completed_at = (
                TechnicalAnalysisStatus.COMPLETED,
                datetime.now(UTC),
            )
            self._session.add(
                MediaTechnicalMetadata(
                    business_id=business_id, asset_id=asset.id, **metadata.__dict__
                )
            )
            for derivative in derivatives:
                self._session.add(
                    MediaDerivative(
                        business_id=business_id,
                        asset_id=asset.id,
                        kind=derivative.kind,
                        storage_object_key=(
                            f"tenant/{business_id}/media/{asset.id}/"
                            f"{derivative.kind}/{uuid4().hex}"
                        ),
                        content_type=derivative.content_type,
                        byte_size=derivative.byte_size,
                        sha256_checksum=derivative.sha256_checksum,
                        status=MediaDerivativeStatus.READY,
                        ready_at=datetime.now(UTC),
                    )
                )
            attempt = await self._operations.get_attempt_for_update(job.id, job.attempt_count)
            if attempt is not None:
                attempt.status = JobAttemptStatus.SUCCEEDED
                attempt.finished_at = datetime.now(UTC)
            job.status, job.finished_at = JobStatus.SUCCEEDED, datetime.now(UTC)
            self._session.add(
                OutboxEvent(
                    business_id=business_id,
                    event_type="media.technical_analysis.completed",
                    aggregate_type="media_asset",
                    aggregate_id=asset.id,
                    payload={"job_id": str(job.id), "asset_id": str(asset.id)},
                    correlation_id=job.correlation_id,
                    status=OutboxStatus.PENDING,
                    max_attempts=job.max_attempts,
                    next_attempt_at=datetime.now(UTC),
                )
            )
            return job

    def _validate_metadata(
        self, *, asset_byte_size: int, metadata: NormalizedTechnicalMetadata
    ) -> None:
        if metadata.file_size != asset_byte_size:
            raise TechnicalPermanentError("TECHNICAL_FILE_SIZE_MISMATCH")
        if metadata.duration_ms > self._settings.media_max_duration_seconds * 1_000:
            raise TechnicalPermanentError("TECHNICAL_DURATION_EXCEEDED")

    async def _fail(
        self, business_id: UUID, job_id: UUID, code: str, *, transient: bool
    ) -> BackgroundJob:
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                raise RuntimeError("technical job disappeared")
            analysis = await self._session.scalar(
                select(MediaTechnicalAnalysis)
                .where(MediaTechnicalAnalysis.asset_id == job.resource_id)
                .with_for_update()
            )
            if analysis is not None:
                analysis.status, analysis.safe_error_code = (TechnicalAnalysisStatus.FAILED, code)
            attempt = await self._operations.get_attempt_for_update(job.id, job.attempt_count)
            if attempt is not None:
                attempt.status = JobAttemptStatus.FAILED
                attempt.finished_at = datetime.now(UTC)
                attempt.error_code = code
                attempt.error_summary = code
            job.last_error_code, job.last_error_summary, job.finished_at = (
                code,
                code,
                datetime.now(UTC),
            )
            if transient and job.attempt_count < job.max_attempts:
                job.status, job.next_attempt_at = (
                    JobStatus.FAILED,
                    datetime.now(UTC) + timedelta(seconds=min(2**job.attempt_count, 60)),
                )
            else:
                job.status, job.next_attempt_at = (
                    JobStatus.DEAD if transient else JobStatus.FAILED,
                    None,
                )
                if analysis is not None:
                    analysis.status = (
                        TechnicalAnalysisStatus.DEAD
                        if transient
                        else TechnicalAnalysisStatus.FAILED
                    )
            return job
