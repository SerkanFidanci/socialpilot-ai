"""The durable render job: claim, materialize, render, persist.

This is the worker half of the content module. It follows the same shape as the media analysis
services — SKIP LOCKED claim, one attempt row per try, transient failures backed off and
exhausted ones dead-lettered — with three differences that matter to this slice.

**Sources are materialized, never fetched.** The worker reuses W09's materializer, so there is
exactly one download path in the system and a signed URL never exists outside the storage
adapter. Each asset lands in its own private subdirectory: the materializer names files after
the object key's extension, so two `.mp4` sources sharing a directory would silently overwrite
each other.

**Validation runs again here.** See the note in `service.py`: between the request and the
render a campaign can expire or a price can be superseded, and the frame must only ever contain
values that were true when it was drawn.

**Nothing in this file calls a model.** The captions come from transcript rows that already
exist, the text comes from tenant records, and the cuts come from the timeline. That is what
makes this slice's render cost exactly zero in provider spend.

Slice 2E adds two things. Speech is materialized alongside the footage — the same one download
path, per-line objects into their own directories — so a timeline carrying a `voiceover` track
finally renders instead of being refused by a capability nobody had implemented. And a successful
render now writes `content.qc.requested`, which is what turns automatic QC from a table scan on a
30-second tick into an event: slice 2D measured that scan at 134 ms per tick over 200k renders and
handed the fix here, because this is the file that knows a render just finished.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.content.models import RenderStatus
from app.modules.content.render import (
    PREVIEW_PROFILE,
    PlannedAudio,
    PlannedCaption,
    PlannedLogo,
    PlannedSegment,
    PlannedText,
    PlannedVoiceover,
    RenderPermanentError,
    RenderPlan,
    RenderPort,
    RenderProfile,
    RenderRequest,
    RenderResult,
    RenderTransientError,
)
from app.modules.content.repository import (
    RENDER_RESOURCE_TYPE,
    ContentFactsReader,
    ContentRepository,
)
from app.modules.content.service import (
    MAX_CAPTIONS,
    ContentTimelineService,
    current_disclosure_state,
)
from app.modules.content.timeline import (
    LOGO_STYLES,
    TEXT_STYLES,
    AudioTrackKind,
    CaptionSource,
    OverlayKind,
    Timeline,
    TimelineSchemaError,
    parse_timeline,
)
from app.modules.content.validation import AssetFacts, ValidationOutcome
from app.modules.media.storage import (
    MultipartStoragePort,
    StoragePermanentError,
    StorageUnavailableError,
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

# The event that wakes automatic QC. Slice 2D had no producer for it and claimed by scanning for
# succeeded renders without a report; that scan stays as a rare sweep, and this is now the
# primary trigger. The envelope carries no measurement and the Celery message carries no
# arguments — the QC worker re-reads everything under its own tenant-scoped claim.
QC_REQUESTED_EVENT = "content.qc.requested"


@dataclass(frozen=True, slots=True)
class _ClaimedRender:
    """Everything the render needs, lifted out of the claim transaction.

    Values only — no ORM instance crosses this boundary, so nothing lazy-loads against a
    session that has since moved on.
    """

    business_id: UUID
    render_id: UUID
    profile: RenderProfile
    correlation_id: str
    timeline: Timeline
    outcome: ValidationOutcome
    facts: dict[UUID, AssetFacts]
    captions: tuple[PlannedCaption, ...]
    # Object keys per voiceover, in line order. Read inside the claim like everything else, so
    # the encode below touches no session.
    voiceover_keys: dict[UUID, tuple[str, ...]]


class ContentRenderService:
    """Drain one render job: validated timeline in, stored objects out."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        materializer: MediaMaterializerPort,
        render: RenderPort,
        storage: MultipartStoragePort,
    ) -> None:
        self._session = session
        self._settings = settings
        self._materializer = materializer
        self._render = render
        self._storage = storage
        self._repository = ContentRepository(session)
        self._facts = ContentFactsReader(session)
        self._operations = OperationsRepository(session)
        self._timelines = ContentTimelineService(session, settings, render)
        self._claimed_attempts: dict[UUID, int] = {}

    async def claim_next(self) -> BackgroundJob | None:
        """Atomically claim a due render job using PostgreSQL SKIP LOCKED."""

        async with self._session.begin():
            job = await self._repository.claim_next_render_job()
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
            self._claimed_attempts[job.id] = job.attempt_count
            return job

    async def process_next(self, *, workdir: Path) -> BackgroundJob | None:
        job = await self.claim_next()
        if job is None:
            return None
        # Sources, intermediates and outputs all live inside this directory and go away with
        # it, success or failure, so the scratch budget in worker/scratch.py stays honest.
        with TemporaryDirectory(prefix="content-render-", dir=workdir) as temporary:
            return await self.process_claimed(
                business_id=job.business_id,
                job_id=job.id,
                workdir=Path(temporary),
                attempt_number=job.attempt_count,
            )

    async def process_claimed(
        self, *, business_id: UUID, job_id: UUID, workdir: Path, attempt_number: int | None = None
    ) -> BackgroundJob:
        """Complete the claimed render, leaving no attempt in STARTED state."""

        expected = attempt_number or self._claimed_attempts.get(job_id)
        if expected is None:
            raise RuntimeError("CONTENT_RENDER_ATTEMPT_UNKNOWN")
        claimed = await self._begin(business_id, job_id, expected)
        if claimed is None:
            job = await self._reload(business_id, job_id)
            return job
        try:
            result, plan = await self._execute(claimed, workdir)
        except RenderTransientError as error:
            return await self._fail(business_id, job_id, expected, str(error), transient=True)
        except RenderPermanentError as error:
            return await self._fail(business_id, job_id, expected, str(error), transient=False)
        except StorageUnavailableError:
            return await self._fail(
                business_id, job_id, expected, "RENDER_STORAGE_UNAVAILABLE", transient=True
            )
        except StoragePermanentError:
            return await self._fail(
                business_id, job_id, expected, "RENDER_STORAGE_METADATA_INVALID", transient=False
            )
        return await self._succeed(business_id, job_id, expected, claimed, result, plan)

    # --- transaction boundaries ---------------------------------------------------------------

    async def _begin(self, business_id: UUID, job_id: UUID, expected: int) -> _ClaimedRender | None:
        """Take the claim and read everything the render needs, in one transaction.

        All database work happens here, before FFmpeg runs. That ordering is deliberate: the
        encode can take minutes, and a session left mid-transaction across it would pin a
        PostgreSQL connection and a snapshot for the whole render. Afterwards the service
        touches only files and object storage until it reopens a transaction to record the
        outcome.
        """

        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                return None
            attempt = await self._operations.get_active_attempt_for_update(job, expected)
            if attempt is None:
                # The claim was lost to recovery or another generation; do nothing quietly.
                return None
            render = await self._repository.get_render_for_job(business_id, job_id)
            if render is None:
                raise RenderPermanentError("RENDER_RECORD_MISSING")
            render.status = RenderStatus.RUNNING
            record = await self._repository.get_timeline(business_id, render.timeline_id)
            if record is None:
                raise RenderPermanentError("RENDER_TIMELINE_MISSING")
            try:
                timeline = parse_timeline(record.document)
            except TimelineSchemaError as error:
                raise RenderPermanentError("RENDER_TIMELINE_INVALID") from error

            # Validated again, here, against the records as they are now — see the module
            # docstring. A campaign that expired since the request must not reach a frame.
            outcome = await self._timelines.validate(business_id, timeline, render.profile)
            if not outcome.ok:
                raise RenderPermanentError("RENDER_TIMELINE_VALIDATION_FAILED")

            return _ClaimedRender(
                business_id=business_id,
                render_id=render.id,
                profile=render.profile,
                correlation_id=job.correlation_id,
                timeline=timeline,
                outcome=outcome,
                facts=await self._facts.asset_facts(business_id, timeline.asset_ids),
                captions=await self._captions(business_id, timeline),
                voiceover_keys=await self._facts.voiceover_object_keys(
                    business_id, timeline.voiceover_ids
                ),
            )

    async def _execute(
        self, claimed: _ClaimedRender, workdir: Path
    ) -> tuple[RenderResult, RenderPlan]:
        """Materialize, render, upload. No database session is touched in here."""

        sources = await self._materialize(claimed.timeline, claimed.facts, workdir)
        speech = await self._materialize_speech(claimed, workdir)
        plan = self._build_plan(claimed, sources, speech)
        result = await self._render.render(
            request=RenderRequest(
                plan=plan,
                workdir=workdir,
                preview_profile=PREVIEW_PROFILE,
                timeout_seconds=self._settings.render_step_timeout_seconds,
            )
        )
        await self._persist_artifacts(claimed.business_id, claimed.render_id, result)
        return (result, plan)

    async def _succeed(
        self,
        business_id: UUID,
        job_id: UUID,
        expected: int,
        claimed: _ClaimedRender,
        result: RenderResult,
        plan: RenderPlan,
    ) -> BackgroundJob:
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                raise RuntimeError("render job disappeared")
            attempt = await self._operations.get_active_attempt_for_update(job, expected)
            if attempt is None:
                return job
            render = await self._repository.get_render(business_id, claimed.render_id, lock=True)
            if render is not None:
                prefix = _object_prefix(business_id, render.id)
                render.status = RenderStatus.SUCCEEDED
                render.master_object_key = f"{prefix}/master"
                render.preview_object_key = f"{prefix}/preview"
                render.thumbnail_object_key = f"{prefix}/thumbnail"
                render.byte_size = result.master.byte_size
                render.duration_ms = result.summary.duration_ms
                render.width = result.summary.width
                render.height = result.summary.height
                render.video_codec = result.summary.video_codec
                render.audio_codec = result.summary.audio_codec
                render.ai_disclosure_state = plan.ai_disclosure
                render.provenance_state = result.provenance
                render.failure_code = None
                render.completed_at = datetime.now(UTC)
                # Written in the transaction that makes the render succeed, so an output that
                # exists and an ask for it to be checked become durable together. A crash before
                # the outbox is dispatched costs one sweep interval, never a render nobody
                # checked — which is the property slice 2D's scan was buying at 134 ms a tick.
                self._operations.add(
                    OutboxEvent(
                        id=uuid4(),
                        business_id=business_id,
                        event_type=QC_REQUESTED_EVENT,
                        aggregate_type=RENDER_RESOURCE_TYPE,
                        aggregate_id=render.id,
                        payload={"render_id": str(render.id)},
                        correlation_id=claimed.correlation_id,
                        status=OutboxStatus.PENDING,
                        max_attempts=job.max_attempts,
                        next_attempt_at=datetime.now(UTC),
                    )
                )
            attempt.status = JobAttemptStatus.SUCCEEDED
            attempt.finished_at = datetime.now(UTC)
            attempt.error_code = None
            attempt.error_summary = None
            job.status = JobStatus.SUCCEEDED
            job.finished_at = datetime.now(UTC)
            job.last_error_code = None
            job.last_error_summary = None
            return job

    async def _fail(
        self, business_id: UUID, job_id: UUID, expected: int, code: str, *, transient: bool
    ) -> BackgroundJob:
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                raise RuntimeError("render job disappeared")
            attempt = await self._operations.get_active_attempt_for_update(job, expected)
            if attempt is None:
                return job
            render = await self._repository.get_render_for_job(business_id, job_id)
            if render is not None:
                render.status = RenderStatus.FAILED
                render.failure_code = code
                render.completed_at = datetime.now(UTC)
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
                # Matching the media services: an exhausted transient failure is dead-lettered,
                # a permanent one stays FAILED with no further attempt scheduled.
                job.status = JobStatus.DEAD if transient else JobStatus.FAILED
                job.next_attempt_at = None
            return job

    async def _reload(self, business_id: UUID, job_id: UUID) -> BackgroundJob:
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                raise RuntimeError("render job disappeared")
            return job

    # --- plan assembly -------------------------------------------------------------------------

    async def _materialize(
        self, timeline: Timeline, facts: dict[UUID, AssetFacts], workdir: Path
    ) -> dict[UUID, Path]:
        """Stream each referenced source into its own private subdirectory.

        Per-asset directories are not tidiness: the materializer derives the destination file
        name from the object key's extension, so two `.mp4` sources in one directory would
        collide and the second would silently replace the first.
        """

        sources: dict[UUID, Path] = {}
        for index, asset_id in enumerate(timeline.asset_ids):
            entry = facts.get(asset_id)
            if entry is None or entry.source_object_key is None:
                raise RenderPermanentError("RENDER_SOURCE_UNAVAILABLE")
            sources[asset_id] = await self._materializer.materialize(
                object_key=entry.source_object_key, workdir=workdir / f"source-{index:03d}"
            )
        return sources

    async def _materialize_speech(self, claimed: _ClaimedRender, workdir: Path) -> tuple[Path, ...]:
        """Stream the timeline's voiceover lines down, in order, one directory each.

        The same reasoning as the video sources: the materializer names files from the object
        key's extension, so two `.wav` lines sharing a directory would collide. A track that
        names a voiceover with no usable audio is a permanent failure here rather than a silent
        render — validation already refused that case, and reaching it means the row changed
        between validation and the encode.
        """

        paths: list[Path] = []
        for index, voiceover_id in enumerate(claimed.timeline.voiceover_ids):
            keys = claimed.voiceover_keys.get(voiceover_id)
            if not keys:
                raise RenderPermanentError("RENDER_VOICEOVER_UNAVAILABLE")
            for line, object_key in enumerate(keys):
                paths.append(
                    await self._materializer.materialize(
                        object_key=object_key,
                        workdir=workdir / f"voice-{index:03d}-{line:03d}",
                    )
                )
        return tuple(paths)

    def _build_plan(
        self, claimed: _ClaimedRender, sources: dict[UUID, Path], speech: tuple[Path, ...]
    ) -> RenderPlan:
        """Turn the claimed facts into a provider-neutral plan. Pure — no I/O."""

        timeline, facts = claimed.timeline, claimed.facts
        resolved = claimed.outcome.resolved_texts
        segments = tuple(
            PlannedSegment(
                asset_id=clip.asset_id,
                source_path=sources[clip.asset_id],
                source_start_ms=clip.source_start_ms,
                source_end_ms=clip.source_end_ms,
                crop_mode=clip.crop_mode,
                transition_out=clip.transition_out,
                has_audio=bool(facts[clip.asset_id].has_audio),
            )
            for clip in timeline.clips
        )
        texts: list[PlannedText] = []
        logos: list[PlannedLogo] = []
        for index, overlay in enumerate(timeline.overlays):
            if overlay.kind is OverlayKind.LOGO and overlay.asset_id is not None:
                logos.append(
                    PlannedLogo(
                        source_path=sources[overlay.asset_id],
                        anchor=overlay.anchor,
                        width_ratio=LOGO_STYLES[overlay.style_id],
                        start_ms=overlay.start_ms,
                        end_ms=overlay.end_ms,
                    )
                )
                continue
            # The string validation checked is the string drawn — never re-resolved here.
            text = resolved.get(index)
            if not text:
                continue
            texts.append(
                PlannedText(
                    text=text,
                    style=TEXT_STYLES[overlay.style_id],
                    anchor=overlay.anchor,
                    start_ms=overlay.start_ms,
                    end_ms=overlay.end_ms,
                )
            )
        audio = next(
            (track for track in timeline.audio_tracks if track.kind is AudioTrackKind.ORIGINAL),
            None,
        )
        voice = next(
            (track for track in timeline.audio_tracks if track.kind is AudioTrackKind.VOICEOVER),
            None,
        )
        return RenderPlan(
            profile=claimed.profile,
            canvas=timeline.canvas,
            segments=segments,
            texts=tuple(texts),
            logos=tuple(logos),
            captions=claimed.captions,
            caption_style=TEXT_STYLES[timeline.captions.style_id],
            audio=PlannedAudio(
                source=AudioTrackKind.ORIGINAL,
                gain_db=audio.gain_db if audio else 0,
                voiceover=(
                    PlannedVoiceover(segment_paths=speech, gain_db=voice.gain_db if voice else 0)
                    if speech
                    else None
                ),
                # Read from the bed's own flag, not assumed: a timeline that places speech over
                # untouched footage is a legitimate document, and the renderer must not decide
                # otherwise on its behalf.
                duck_under_voice=bool(audio and audio.duck_under_voice and speech),
            ),
            ai_disclosure=current_disclosure_state(),
        )

    async def _captions(self, business_id: UUID, timeline: Timeline) -> tuple[PlannedCaption, ...]:
        """Project stored transcript segments onto timeline time, one clip at a time.

        A clip takes a window out of its source, so a transcript segment only contributes the
        part of itself that survives the cut, shifted to where the clip sits on the timeline.
        Reading persisted rows is not an AI call — the text was produced upstream.
        """

        if (
            not timeline.captions.enabled
            or timeline.captions.source is not CaptionSource.TRANSCRIPT
        ):
            return ()
        cues: list[PlannedCaption] = []
        cache: dict[UUID, tuple[tuple[int, int, str], ...]] = {}
        for clip in timeline.clips:
            if clip.asset_id not in cache:
                cache[clip.asset_id] = await self._facts.transcript_segments(
                    business_id, clip.asset_id
                )
            for start_ms, end_ms, text in cache[clip.asset_id]:
                overlap_start = max(start_ms, clip.source_start_ms)
                overlap_end = min(end_ms, clip.source_end_ms)
                if overlap_end <= overlap_start or not text.strip():
                    continue
                cues.append(
                    PlannedCaption(
                        text=text,
                        start_ms=clip.timeline_start_ms + overlap_start - clip.source_start_ms,
                        end_ms=clip.timeline_start_ms + overlap_end - clip.source_start_ms,
                    )
                )
                if len(cues) >= MAX_CAPTIONS:
                    return tuple(cues)
        return tuple(cues)

    async def _persist_artifacts(
        self, business_id: UUID, render_id: UUID, result: RenderResult
    ) -> None:
        """Upload each artifact and verify storage observed exactly what was produced."""

        prefix = _object_prefix(business_id, render_id)
        for artifact in (result.master, result.preview, result.thumbnail):
            metadata = await self._storage.persist_file(
                object_key=f"{prefix}/{artifact.kind}",
                source_path=artifact.path,
                content_type=artifact.content_type,
            )
            if (
                metadata.byte_size != artifact.byte_size
                or metadata.content_type.lower() != artifact.content_type
                or metadata.sha256_checksum.lower() != artifact.sha256_checksum
            ):
                raise StoragePermanentError("render artifact metadata mismatch")


def _object_prefix(business_id: UUID, render_id: UUID) -> str:
    return f"tenant/{business_id}/renders/{render_id}"
