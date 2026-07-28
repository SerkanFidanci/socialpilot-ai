"""Tenant-safe scene and speech analysis contracts and worker service."""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.modules.media.models import (
    MediaDerivative,
    MediaDerivativeStatus,
    MediaScene,
    TechnicalAnalysisStatus,
    Transcript,
    TranscriptSegment,
    TranscriptStatus,
)
from app.modules.media.repository import MediaRepository
from app.modules.media.storage import (
    MultipartStoragePort,
    StoragePermanentError,
    StorageUnavailableError,
    StoredObjectMetadata,
)
from app.modules.media.technical import MediaMaterializerPort
from app.modules.operations.models import (
    BackgroundJob,
    JobAttempt,
    JobAttemptStatus,
    JobStatus,
    OutboxEvent,
    OutboxStatus,
)
from app.modules.operations.repository import OperationsRepository


@dataclass(frozen=True)
class SceneCandidate:
    start_ms: int
    end_ms: int
    confidence: float


@dataclass(frozen=True)
class TranscriptCandidate:
    start_ms: int
    end_ms: int
    text: str
    confidence: float
    speaker_label: str | None = None


@dataclass(frozen=True)
class SpeechResult:
    language: str
    provider: str
    segments: tuple[TranscriptCandidate, ...]


@dataclass(frozen=True)
class AudioOutput:
    path: Path
    byte_size: int
    sha256_checksum: str


class SceneDetectionPort(Protocol):
    async def detect(self, *, proxy_path: Path, duration_ms: int) -> tuple[SceneCandidate, ...]: ...


class AudioExtractionPort(Protocol):
    async def extract(
        self, *, input_path: Path, output_dir: Path, timeout_seconds: int
    ) -> AudioOutput: ...


class SpeechToTextPort(Protocol):
    async def transcribe(self, *, audio_path: Path, timeout_seconds: int) -> SpeechResult: ...


class SceneSpeechTransientError(RuntimeError):
    pass


class SceneSpeechPermanentError(RuntimeError):
    pass


class FFmpegAudioExtractionAdapter(AudioExtractionPort):
    """Extract bounded mono WAV audio with a fixed binary and no shell."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def extract(
        self, *, input_path: Path, output_dir: Path, timeout_seconds: int
    ) -> AudioOutput:
        output_path = output_dir / "audio.wav"
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    self._settings.ffmpeg_binary,
                    "-y",
                    "-i",
                    str(input_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(output_path),
                ],
                shell=False,
                cwd=output_dir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise SceneSpeechTransientError("AUDIO_EXTRACTION_TIMEOUT") from error
        if result.returncode != 0 or len(result.stderr) > 16_384:
            raise SceneSpeechPermanentError("AUDIO_EXTRACTION_FAILED")
        try:
            byte_size = output_path.stat().st_size
        except OSError as error:
            raise SceneSpeechPermanentError("AUDIO_EXTRACTION_FAILED") from error
        if byte_size <= 0 or byte_size > self._settings.media_max_extracted_audio_bytes:
            raise SceneSpeechPermanentError("AUDIO_OUTPUT_SIZE_INVALID")
        digest = hashlib.sha256()
        with output_path.open("rb") as audio_file:
            while chunk := audio_file.read(1_048_576):
                digest.update(chunk)
        return AudioOutput(output_path, byte_size, digest.hexdigest())


def normalize_scenes(
    *, settings: Settings, duration_ms: int, scenes: tuple[SceneCandidate, ...]
) -> tuple[SceneCandidate, ...]:
    if not scenes:
        return (SceneCandidate(0, duration_ms, 1.0),)
    if len(scenes) > settings.scene_max_count:
        raise SceneSpeechPermanentError("SCENE_COUNT_EXCEEDED")
    previous_end = 0
    normalized: list[SceneCandidate] = []
    for scene in scenes:
        if (
            scene.start_ms < 0
            or scene.end_ms <= scene.start_ms
            or scene.end_ms > duration_ms
            or scene.start_ms < previous_end
            or scene.end_ms - scene.start_ms < settings.scene_min_duration_ms
            or not 0.0 <= scene.confidence <= 1.0
        ):
            raise SceneSpeechPermanentError("SCENE_TIMECODE_INVALID")
        normalized.append(scene)
        previous_end = scene.end_ms
    return tuple(normalized)


def normalize_transcript(
    *, settings: Settings, duration_ms: int, result: SpeechResult
) -> tuple[TranscriptCandidate, ...]:
    if (
        not result.language
        or not result.provider
        or len(result.segments) > settings.transcript_max_segment_count
    ):
        raise SceneSpeechPermanentError("TRANSCRIPT_INVALID")
    previous_end = 0
    normalized: list[TranscriptCandidate] = []
    for segment in result.segments:
        normalized_text = normalize_safe_text(segment.text)
        if (
            segment.start_ms < 0
            or segment.end_ms <= segment.start_ms
            or segment.end_ms > duration_ms
            or segment.start_ms < previous_end
            or not settings.transcript_min_confidence <= segment.confidence <= 1.0
        ):
            raise SceneSpeechPermanentError("TRANSCRIPT_TIMECODE_INVALID")
        if not normalized_text or len(normalized_text) > settings.transcript_max_segment_chars:
            raise SceneSpeechPermanentError("TRANSCRIPT_INVALID")
        normalized.append(
            TranscriptCandidate(
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=normalized_text,
                confidence=segment.confidence,
                speaker_label=segment.speaker_label,
            )
        )
        previous_end = segment.end_ms
    normalized_segments = tuple(normalized)
    if len(transcript_full_text(normalized_segments)) > settings.transcript_max_total_chars:
        raise SceneSpeechPermanentError("TRANSCRIPT_INVALID")
    return normalized_segments


def normalize_safe_text(value: str) -> str:
    """Accept ordinary spaces/newlines while rejecting PostgreSQL-unsafe controls."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise SceneSpeechPermanentError("TRANSCRIPT_INVALID")
    for character in normalized:
        if character in {"\n", "\t"}:
            continue
        if unicodedata.category(character).startswith("C"):
            raise SceneSpeechPermanentError("TRANSCRIPT_INVALID")
    return normalized


def transcript_full_text(segments: tuple[TranscriptCandidate, ...]) -> str:
    """Use the same bounded representation for validation and persistence."""

    return " ".join(segment.text for segment in segments)


class SceneSpeechAnalysisService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        materializer: MediaMaterializerPort,
        scene_detector: SceneDetectionPort,
        audio_extractor: AudioExtractionPort,
        speech_to_text: SpeechToTextPort,
        storage: MultipartStoragePort,
    ) -> None:
        self._session, self._settings = session, settings
        self._materializer, self._scene_detector = materializer, scene_detector
        self._audio_extractor, self._speech_to_text, self._storage = (
            audio_extractor,
            speech_to_text,
            storage,
        )
        self._media, self._operations = MediaRepository(session), OperationsRepository(session)

    async def claim_next(self) -> BackgroundJob | None:
        async with self._session.begin():
            job = await self._operations.claim_next_scene_speech_job()
            if job is None:
                return None
            job.status = JobStatus.RUNNING
            job.attempt_count += 1
            job.started_at = datetime.now(UTC)
            job.finished_at = None
            job.next_attempt_at = None
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
        with TemporaryDirectory(prefix="scene-speech-", dir=workdir) as temporary_directory:
            return await self.process_claimed(
                business_id=job.business_id, job_id=job.id, workdir=Path(temporary_directory)
            )

    async def process_claimed(
        self, *, business_id: UUID, job_id: UUID, workdir: Path
    ) -> BackgroundJob:
        try:
            async with self._session.begin():
                job = await self._operations.get_job_for_update(business_id, job_id)
                if (
                    job is None
                    or job.job_type != "media.scene_speech_analysis"
                    or job.status != JobStatus.RUNNING
                ):
                    raise self._not_found()
                asset = await self._media.get_asset(business_id, job.resource_id, lock=True)
                analysis = await self._media.get_technical_analysis(
                    business_id, job.resource_id, lock=True
                )
                metadata = await self._media.get_technical_metadata(business_id, job.resource_id)
                proxy = await self._media.get_derivative(
                    business_id, job.resource_id, "proxy", lock=True
                )
                if (
                    asset is None
                    or analysis is None
                    or analysis.status != TechnicalAnalysisStatus.COMPLETED
                    or metadata is None
                    or proxy is None
                    or proxy.status != MediaDerivativeStatus.READY
                ):
                    raise SceneSpeechPermanentError("SCENE_SPEECH_RESOURCE_STATE_INVALID")
                proxy_key, duration_ms, has_audio, timeout = (
                    proxy.storage_object_key,
                    metadata.duration_ms,
                    metadata.has_audio,
                    job.timeout_seconds,
                )
            proxy_path = await self._materializer.materialize(object_key=proxy_key, workdir=workdir)
            scenes = normalize_scenes(
                settings=self._settings,
                duration_ms=duration_ms,
                scenes=await self._scene_detector.detect(
                    proxy_path=proxy_path, duration_ms=duration_ms
                ),
            )
            audio_output: AudioOutput | None = None
            speech_result: SpeechResult | None = None
            if has_audio:
                audio_output = await self._audio_extractor.extract(
                    input_path=proxy_path,
                    output_dir=workdir,
                    timeout_seconds=min(timeout, self._settings.audio_extraction_timeout_seconds),
                )
                audio_metadata = await self._persist_audio(
                    business_id, job.resource_id, audio_output
                )
                speech_result = await self._speech_to_text.transcribe(
                    audio_path=audio_output.path, timeout_seconds=self._settings.asr_timeout_seconds
                )
                segments = normalize_transcript(
                    settings=self._settings, duration_ms=duration_ms, result=speech_result
                )
            else:
                segments = ()
                audio_metadata = None
            return await self._persist_results(
                business_id=business_id,
                job_id=job_id,
                duration_ms=duration_ms,
                scenes=scenes,
                speech_result=speech_result,
                segments=segments,
                audio_output=audio_output,
                audio_metadata=audio_metadata,
            )
        except (StorageUnavailableError, SceneSpeechTransientError):
            return await self._fail(
                business_id, job_id, "SCENE_SPEECH_DEPENDENCY_UNAVAILABLE", True
            )
        except StoragePermanentError:
            return await self._fail(business_id, job_id, "SCENE_SPEECH_VALIDATION_FAILED", False)
        except SceneSpeechPermanentError as error:
            return await self._fail(business_id, job_id, str(error), False)
        except ProblemException:
            return await self._fail(
                business_id, job_id, "SCENE_SPEECH_RESOURCE_STATE_INVALID", False
            )
        except IntegrityError:
            return await self._fail(business_id, job_id, "SCENE_SPEECH_PERSISTENCE_FAILED", False)
        except SQLAlchemyError:
            return await self._fail(
                business_id, job_id, "SCENE_SPEECH_PERSISTENCE_UNAVAILABLE", True
            )

    async def _persist_results(
        self,
        *,
        business_id: UUID,
        job_id: UUID,
        duration_ms: int,
        scenes: tuple[SceneCandidate, ...],
        speech_result: SpeechResult | None,
        segments: tuple[TranscriptCandidate, ...],
        audio_output: AudioOutput | None,
        audio_metadata: StoredObjectMetadata | None,
    ) -> BackgroundJob:
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                raise RuntimeError("scene speech job disappeared")
            existing = await self._media.get_transcript(business_id, job.resource_id, lock=True)
            if existing is not None:
                from app.modules.media.video_understanding_service import (
                    VideoUnderstandingSchedulingService,
                )

                await VideoUnderstandingSchedulingService(
                    self._session, self._settings
                ).schedule_after_scene_speech(
                    business_id=business_id,
                    asset_id=job.resource_id,
                    correlation_id=job.correlation_id,
                )
                return await self._mark_succeeded(job)
            transcript = Transcript(
                business_id=business_id,
                asset_id=job.resource_id,
                language=speech_result.language if speech_result else "und",
                duration_ms=duration_ms,
                full_text=transcript_full_text(segments),
                provider=speech_result.provider if speech_result else "none",
                status=TranscriptStatus.COMPLETED if speech_result else TranscriptStatus.NO_SPEECH,
            )
            self._session.add(transcript)
            await self._session.flush()
            for index, scene in enumerate(scenes):
                self._session.add(
                    MediaScene(
                        business_id=business_id,
                        asset_id=job.resource_id,
                        scene_index=index,
                        start_ms=scene.start_ms,
                        end_ms=scene.end_ms,
                        duration_ms=scene.end_ms - scene.start_ms,
                        confidence=scene.confidence,
                    )
                )
            await self._session.flush()
            if audio_output is not None and audio_metadata is not None:
                self._session.add(
                    MediaDerivative(
                        business_id=business_id,
                        asset_id=job.resource_id,
                        kind="audio",
                        storage_object_key=f"tenant/{business_id}/media/{job.resource_id}/derivatives/audio",
                        content_type=audio_metadata.content_type,
                        byte_size=audio_metadata.byte_size,
                        sha256_checksum=audio_metadata.sha256_checksum,
                        status=MediaDerivativeStatus.READY,
                        ready_at=datetime.now(UTC),
                    )
                )
            for index, segment in enumerate(segments):
                self._session.add(
                    TranscriptSegment(
                        transcript_id=transcript.id,
                        segment_index=index,
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        text=segment.text,
                        confidence=segment.confidence,
                        speaker_label=segment.speaker_label,
                    )
                )
            await self._mark_succeeded(job)
            from app.modules.media.video_understanding_service import (
                VideoUnderstandingSchedulingService,
            )

            await VideoUnderstandingSchedulingService(
                self._session, self._settings
            ).schedule_after_scene_speech(
                business_id=business_id,
                asset_id=job.resource_id,
                correlation_id=job.correlation_id,
            )
            self._session.add(
                OutboxEvent(
                    business_id=business_id,
                    event_type="media.scene_speech.completed",
                    aggregate_type="media_asset",
                    aggregate_id=job.resource_id,
                    payload={"job_id": str(job.id), "asset_id": str(job.resource_id)},
                    correlation_id=job.correlation_id,
                    status=OutboxStatus.PENDING,
                    max_attempts=job.max_attempts,
                    next_attempt_at=datetime.now(UTC),
                )
            )
            return job

    async def _mark_succeeded(self, job: BackgroundJob) -> BackgroundJob:
        attempt = await self._operations.get_attempt_for_update(job.id, job.attempt_count)
        if attempt is not None:
            attempt.status = JobAttemptStatus.SUCCEEDED
            attempt.finished_at = datetime.now(UTC)
            attempt.error_code = None
            attempt.error_summary = None
        job.status = JobStatus.SUCCEEDED
        job.finished_at = datetime.now(UTC)
        job.last_error_code = None
        job.last_error_summary = None
        return job

    async def _persist_audio(
        self, business_id: UUID, asset_id: UUID, audio: AudioOutput
    ) -> StoredObjectMetadata:
        key = f"tenant/{business_id}/media/{asset_id}/derivatives/audio"
        metadata = await self._storage.persist_file(
            object_key=key, source_path=audio.path, content_type="audio/wav"
        )
        if (
            metadata.byte_size != audio.byte_size
            or metadata.content_type.lower() != "audio/wav"
            or metadata.sha256_checksum != audio.sha256_checksum
        ):
            raise StoragePermanentError("audio metadata mismatch")
        return metadata

    async def _fail(
        self, business_id: UUID, job_id: UUID, code: str, transient: bool
    ) -> BackgroundJob:
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                raise RuntimeError("scene speech job disappeared")
            attempt = await self._operations.get_attempt_for_update(job.id, job.attempt_count)
            if attempt is not None:
                attempt.status = JobAttemptStatus.FAILED
                attempt.finished_at = datetime.now(UTC)
                attempt.error_code = code
                attempt.error_summary = code
            job.last_error_code = code
            job.last_error_summary = code
            job.finished_at = datetime.now(UTC)
            if transient and job.attempt_count < job.max_attempts:
                job.status = JobStatus.FAILED
                job.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=min(2**job.attempt_count, 60)
                )
            else:
                job.status = JobStatus.DEAD if transient else JobStatus.FAILED
                job.next_attempt_at = None
            return job

    @staticmethod
    def _not_found() -> ProblemException:
        return ProblemException(
            status=404,
            code="TENANT_RESOURCE_NOT_FOUND",
            title="Resource not found",
            detail="The requested resource is not available.",
        )
