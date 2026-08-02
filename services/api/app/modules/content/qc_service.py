"""The durable QC job: claim a finished render, measure it, record a judgement. Nothing else.

This is the worker half of slice 2D and it follows the render job's shape — SKIP LOCKED claim,
one attempt row per try, transient failures backed off and exhausted ones settled — with four
differences that carry the slice's decisions.

**QC is driven by absence, not by an event.** The claim looks for a succeeded render that has no
report. Nothing in the render path had to change to make automatic QC exist, and a render that
finished while this worker was down is picked up when it returns rather than depending on a
queue entry that may not have survived. `NOT EXISTS` over *any* report — not only a completed
one — is what keeps automatic QC to one run per render, so a permanently failed run stays
dead-lettered instead of being retried forever.

**A run that cannot finish still produces a report.** When attempts are exhausted the row is
settled `failed` with every check `unknown`, verdict `needs_review` and a failure code. The
alternative — leaving the row `pending` — would mean a render nobody ever checked and nobody
could see had not been checked, which is the exact shape of the problem QC exists to remove.

**A failure of the measurement is not a failure of the video, and vice versa.** A file the probe
cannot parse answers "video açılıyor mu" with `failed`; a probe that could not run answers it
with `unknown`. The first is a verdict, the second is an outage, and the two must not be spelled
the same way.

**Nothing here re-renders, re-routes, or counts a retry of the render.** The report names a
suggested path and stops. Attempt limits and lifecycle transitions belong to slice 2E, which is
where a loop can be bounded; binding them here would put an unbounded render loop inside the
component whose only job is to be trustworthy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.modules.businesses.repository import BusinessRepository
from app.modules.content.models import RenderOutput, RenderQcReport
from app.modules.content.policy import ContentAction, permits_action
from app.modules.content.qc import (
    CODE_MEASUREMENT_UNAVAILABLE,
    CODE_PROVIDER_DISABLED,
    CODE_PROVIDER_FAILED,
    CODE_PROVIDER_UNAVAILABLE,
    CODE_VERIFIED_VALUE_UNRESOLVABLE,
    MODEL_CHECKS,
    VISUAL_QC_CAPABILITY,
    CheckResult,
    CheckStatus,
    MediaQcProbePermanentError,
    MediaQcProbePort,
    MediaQcProbeTransientError,
    OverlayTextFact,
    QcCheck,
    QcFacts,
    QcMeasurement,
    QcProbeRequest,
    QcRunStatus,
    QcThresholds,
    QcVerdict,
    RemediationPath,
    RouteSnapshot,
    VerifiedSourceAudit,
    VisualQcDisabledError,
    VisualQcPermanentError,
    VisualQcPort,
    VisualQcReport,
    VisualQcRequest,
    VisualQcTransientError,
    audit_verified_sources,
    build_results,
    decide,
    evaluate_deterministic,
    model_check_results,
    serialize_results,
)
from app.modules.content.repository import (
    QC_JOB_TYPE,
    QC_RESOURCE_TYPE,
    ContentFactsReader,
    ContentRepository,
)
from app.modules.content.timeline import (
    TEXT_STYLES,
    OverlayKind,
    Timeline,
    TimelineSchemaError,
    parse_timeline,
)
from app.modules.content.validation import ValidationContext, resolve_overlay_text
from app.modules.media.storage import StoragePermanentError, StorageUnavailableError
from app.modules.media.technical import MediaMaterializerPort
from app.modules.operations.models import (
    BackgroundJob,
    JobAttempt,
    JobAttemptStatus,
    JobStatus,
    ProviderUsage,
)
from app.modules.operations.repository import OperationsRepository

# The measurement could not be taken because the object itself could not be read. Distinct from
# `QC_CONTAINER_UNREADABLE`, which is a statement about the bytes rather than about reaching them.
CODE_OUTPUT_UNREACHABLE = "QC_CONTAINER_UNREADABLE"


def thresholds_from(settings: Settings) -> QcThresholds:
    """Freeze the deployment's configured thresholds into the value a report stores."""

    return QcThresholds(
        version=settings.qc_ruleset_version,
        duration_tolerance_ms=settings.qc_duration_tolerance_ms,
        loudness_target_lufs=settings.qc_loudness_target_lufs,
        loudness_tolerance_lu=settings.qc_loudness_tolerance_lu,
        silence_floor_lufs=settings.qc_silence_floor_lufs,
        black_ratio_limit=settings.qc_black_ratio_limit,
        static_ratio_limit=settings.qc_static_ratio_limit,
        unusable_source_ratio=settings.qc_unusable_source_ratio,
        speech_drift_ms=settings.qc_speech_drift_ms,
    )


class ContentQcReportService:
    """The read side: authorize, then hand back one render's report. No measurement, no ports.

    Separate from `ContentQcService` because the API must not be able to construct a QC *run*.
    The worker service takes a materializer, a probe and a vision adapter; a controller holding
    those could start a measurement inside a request, which is exactly the FFmpeg-in-the-API
    shape this slice is required not to have. Authorization is spelled out here rather than
    inherited, following the pattern the script and voiceover services already set.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = ContentRepository(session)
        self._businesses = BusinessRepository(session)

    async def get_report(
        self, *, user_id: UUID, business_id: UUID, render_id: UUID
    ) -> RenderQcReport:
        """Membership first, then permission: an outsider gets `404`, a member gets `403`."""

        membership = await self._businesses.get_active_membership(business_id, user_id)
        if membership is None:
            raise _not_found("BUSINESS_NOT_FOUND", "Business not found")
        if not permits_action(membership.role, ContentAction.RENDER_READ):
            raise ProblemException(
                status=403,
                code="INSUFFICIENT_PERMISSION",
                title="Forbidden",
                detail="You do not have this permission.",
            )
        # Reading a render's QC report is reading the render, so it rides on the render's own
        # permission rather than growing a second one. Both are `business.read`.
        render = await self._repository.get_render(business_id, render_id)
        if render is None:
            # Another tenant's real render id answers exactly like a made-up one: the query is
            # tenant-scoped, so the two are indistinguishable by construction.
            raise _not_found("RENDER_NOT_FOUND", "Render not found")
        report = await self._repository.latest_qc_report(business_id, render_id)
        if report is None:
            raise _not_found("RENDER_QC_REPORT_NOT_FOUND", "Quality control report not found")
        return report


def _not_found(code: str, title: str) -> ProblemException:
    return ProblemException(
        status=404, code=code, title=title, detail="The resource is not available."
    )


@dataclass(frozen=True, slots=True)
class _ClaimedQc:
    """Everything the run needs, lifted out of the claim transaction as values only."""

    business_id: UUID
    report_id: UUID
    render_id: UUID
    correlation_id: str
    master_object_key: str
    facts: QcFacts
    expects_logo: bool


class ContentQcService:
    """Drain one QC job: a finished render in, a report with a verdict out."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        materializer: MediaMaterializerPort,
        probe: MediaQcProbePort,
        visual_qc: VisualQcPort,
    ) -> None:
        self._session = session
        self._settings = settings
        self._materializer = materializer
        self._probe = probe
        self._visual_qc = visual_qc
        self._repository = ContentRepository(session)
        self._facts = ContentFactsReader(session)
        self._operations = OperationsRepository(session)
        self._claimed_attempts: dict[UUID, int] = {}

    # --- claim ---------------------------------------------------------------------------------

    async def claim_next(self) -> BackgroundJob | None:
        """Claim one unchecked render and open its job and its report in the same transaction.

        The report row is created here, `pending`, and it is what makes the claim idempotent:
        from the moment this commits, `claim_next_unchecked_render` no longer sees this render.
        Its judgement columns already read `needs_review`/`human_review`, so a process killed on
        the next line leaves a record that is pessimistic rather than one that is absent.
        """

        async with self._session.begin():
            render = await self._repository.claim_next_unchecked_render()
            if render is None:
                return None
            report = RenderQcReport(
                id=uuid4(),
                business_id=render.business_id,
                render_id=render.id,
                job_id=None,
                status=QcRunStatus.PENDING,
                # Fail-closed from the first byte written: an unfinished run is unreviewed.
                verdict=QcVerdict.NEEDS_REVIEW,
                recommended_path=RemediationPath.HUMAN_REVIEW,
                checks=serialize_results(build_results(())),
                measurement={},
                qc_version=self._settings.qc_ruleset_version,
                thresholds=thresholds_from(self._settings).as_document(),
                route_snapshot=self._route().as_document(),
                correlation_id=render.correlation_id,
            )
            self._repository.add(report)
            job = BackgroundJob(
                id=uuid4(),
                business_id=render.business_id,
                job_type=QC_JOB_TYPE,
                resource_type=QC_RESOURCE_TYPE,
                resource_id=report.id,
                status=JobStatus.RUNNING,
                attempt_count=1,
                started_at=datetime.now(UTC),
                timeout_seconds=self._settings.qc_job_timeout_seconds,
                max_attempts=self._settings.qc_max_attempts,
                correlation_id=render.correlation_id,
            )
            self._operations.add(job)
            await self._session.flush()
            report.job_id = job.id
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

    async def claim_retry(self) -> BackgroundJob | None:
        """Re-claim a QC job whose earlier attempt failed transiently and whose backoff elapsed."""

        async with self._session.begin():
            job = await self._repository.claim_next_qc_retry()
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
        """Drain one QC job, retries first so a backed-off run is not starved by new work."""

        job = await self.claim_retry() or await self.claim_next()
        if job is None:
            return None
        # The materialized master, the metadata dumps and the sampled frames all live inside this
        # directory and go away with it, success or failure, so the scratch budget stays honest.
        with TemporaryDirectory(prefix="content-qc-", dir=workdir) as temporary:
            return await self.process_claimed(
                business_id=job.business_id,
                job_id=job.id,
                workdir=Path(temporary),
                attempt_number=job.attempt_count,
            )

    # --- the run -------------------------------------------------------------------------------

    async def process_claimed(
        self, *, business_id: UUID, job_id: UUID, workdir: Path, attempt_number: int | None = None
    ) -> BackgroundJob:
        expected = attempt_number or self._claimed_attempts.get(job_id)
        if expected is None:
            raise RuntimeError("CONTENT_QC_ATTEMPT_UNKNOWN")
        claimed = await self._begin(business_id, job_id, expected)
        if claimed is None:
            return await self._reload(business_id, job_id)

        measurement: QcMeasurement | None = None
        measurement_error: str | None = None
        # Why the *run* failed, as opposed to what it found. The two are different questions and
        # the report answers both in different columns: an outage must never read as a bad video.
        run_failure: str | None = None
        try:
            measurement = await self._measure(claimed, workdir)
        except MediaQcProbePermanentError:
            # The bytes are not media this pipeline can read. That is an answer about the output,
            # not an outage, so the run *completes* and `container_readable` fails.
            measurement_error = CODE_OUTPUT_UNREACHABLE
        except StoragePermanentError:
            measurement_error = CODE_OUTPUT_UNREACHABLE
        except (MediaQcProbeTransientError, StorageUnavailableError) as error:
            # Nothing was learned. Retry while attempts remain; when they do not, settle a report
            # that says the output was never measured rather than leaving a `pending` row that
            # nobody will look at again.
            retried = await self._retry(business_id, job_id, expected)
            if retried is not None:
                return retried
            measurement_error = CODE_MEASUREMENT_UNAVAILABLE
            run_failure = str(error) or CODE_MEASUREMENT_UNAVAILABLE

        visual, visual_code, usage = await self._inspect(claimed, measurement)
        results = build_results(
            evaluate_deterministic(
                facts=claimed.facts,
                measurement=measurement,
                thresholds=thresholds_from(self._settings),
                measurement_error=measurement_error,
            )
            + self._model_results(claimed, visual, visual_code)
        )
        return await self._settle(
            business_id,
            job_id,
            expected,
            claimed,
            results=results,
            measurement=measurement,
            usage=usage,
            run_failure=run_failure,
        )

    async def _measure(self, claimed: _ClaimedQc, workdir: Path) -> QcMeasurement:
        """Stream the master out of storage and measure it. No database session is touched."""

        source = await self._materializer.materialize(
            object_key=claimed.master_object_key, workdir=workdir / "output"
        )
        return await self._probe.measure(
            request=QcProbeRequest(
                path=source,
                workdir=source.parent,
                frame_sample_count=self._settings.qc_frame_sample_count,
                frame_max_width=self._settings.qc_frame_max_width,
                timeout_seconds=self._settings.qc_probe_timeout_seconds,
            )
        )

    async def _inspect(
        self, claimed: _ClaimedQc, measurement: QcMeasurement | None
    ) -> tuple[VisualQcReport | None, str | None, ProviderUsage | None]:
        """Ask the vision adapter the four model questions, or record why nobody asked.

        Every failure here leaves the model checks `unknown` and lets the run complete. Retrying
        the whole job because the optional half was unavailable would throw away the
        deterministic measurements that did succeed, and those are the ones that work today.
        """

        requested = tuple(
            check
            for check in MODEL_CHECKS
            if check is not QcCheck.LOGO_VISIBLE or claimed.expects_logo
        )
        if measurement is None or not measurement.frames or not requested:
            return (None, CODE_MEASUREMENT_UNAVAILABLE, None)

        descriptor = self._visual_qc.descriptor
        if descriptor.estimated_cost_minor > self._settings.visual_qc_max_cost_minor:
            # Checked before the call, never after: a ceiling enforced on the way back has
            # already been paid.
            return (None, "QC_VISUAL_COST_LIMIT_EXCEEDED", None)

        started = time.monotonic()
        try:
            report = await self._visual_qc.inspect(
                request=VisualQcRequest(
                    frames=measurement.frames,
                    checks=requested,
                    expects_logo=claimed.expects_logo,
                    max_frames=self._settings.qc_frame_sample_count,
                ),
                timeout_seconds=self._settings.visual_qc_timeout_seconds,
            )
        except VisualQcDisabledError:
            # No call happened, so no usage row: the deployment has no vision provider and this
            # is the normal state until W08's benchmark picks one.
            return (None, CODE_PROVIDER_DISABLED, None)
        except VisualQcTransientError:
            return (
                None,
                CODE_PROVIDER_UNAVAILABLE,
                self._usage(claimed, 0, started, outcome="failed"),
            )
        except VisualQcPermanentError:
            return (
                None,
                CODE_PROVIDER_FAILED,
                self._usage(claimed, 0, started, outcome="failed"),
            )
        return (
            report,
            None,
            self._usage(claimed, report.actual_cost_minor, started, outcome="succeeded"),
        )

    def _model_results(
        self, claimed: _ClaimedQc, report: VisualQcReport | None, code: str | None
    ) -> tuple[CheckResult, ...]:
        """Fold the provider's findings onto the model checks, filling every gap.

        Logo visibility for a render that draws no logo is `passed` with `applicable: false`, and
        that is a different statement from `unknown`: the timeline says there is no logo, so
        there is nothing that could be invisible. Not applicable is a known state of the
        document; not measured is nobody having looked. Only the second one is `unknown`.
        """

        requested = tuple(
            check
            for check in MODEL_CHECKS
            if check is not QcCheck.LOGO_VISIBLE or claimed.expects_logo
        )
        results = list(model_check_results(report, requested=requested, code=code))
        if not claimed.expects_logo:
            results.append(
                CheckResult(
                    check=QcCheck.LOGO_VISIBLE,
                    status=CheckStatus.PASSED,
                    measured={"applicable": False},
                )
            )
        return tuple(results)

    # --- transaction boundaries ----------------------------------------------------------------

    async def _begin(self, business_id: UUID, job_id: UUID, expected: int) -> _ClaimedQc | None:
        """Read everything the run needs in one transaction, before any measurement starts.

        All database work happens here for the same reason it does in the render job: measuring a
        three-minute output takes real time, and a session left open across it would pin a
        PostgreSQL connection and a snapshot for the whole run.
        """

        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                return None
            attempt = await self._operations.get_active_attempt_for_update(job, expected)
            if attempt is None:
                # The claim was lost to recovery or another generation; do nothing quietly.
                return None
            report = await self._repository.get_qc_report_for_job(business_id, job_id)
            if report is None:
                raise RuntimeError("CONTENT_QC_REPORT_MISSING")
            render = await self._repository.get_render(business_id, report.render_id)
            if render is None or render.master_object_key is None:
                raise RuntimeError("CONTENT_QC_RENDER_MISSING")
            timeline_record = await self._repository.get_timeline(business_id, render.timeline_id)
            if timeline_record is None:
                raise RuntimeError("CONTENT_QC_TIMELINE_MISSING")
            try:
                timeline = parse_timeline(timeline_record.document)
            except TimelineSchemaError as error:
                raise RuntimeError("CONTENT_QC_TIMELINE_INVALID") from error
            facts = await self._gather(business_id, render, timeline)
            return _ClaimedQc(
                business_id=business_id,
                report_id=report.id,
                render_id=render.id,
                correlation_id=job.correlation_id,
                master_object_key=render.master_object_key,
                facts=facts,
                expects_logo=any(overlay.kind is OverlayKind.LOGO for overlay in timeline.overlays),
            )

    async def _gather(self, business_id: UUID, render: RenderOutput, timeline: Timeline) -> QcFacts:
        """Assemble the tenant facts the deterministic checks are judged against."""

        now = datetime.now(UTC)
        references = [
            (overlay.text_source, overlay.reference_id)
            for overlay in timeline.overlays
            if overlay.text_source is not None
            and overlay.text_source.is_verified
            and overlay.reference_id is not None
        ]
        context = ValidationContext(
            assets={},
            logo_asset_ids=frozenset(),
            forbidden_terms=(),
            verified_values=await self._facts.verified_values(business_id, references, now=now),
            now=now,
        )
        overlay_texts: list[OverlayTextFact] = []
        for index, overlay in enumerate(timeline.overlays):
            if overlay.kind is OverlayKind.LOGO:
                continue
            pointer = f"$.overlays[{index}]"
            text, _ = resolve_overlay_text(overlay, context=context, pointer=pointer)
            if not text:
                # A reference that no longer resolves has no string to lay out. The verified
                # audit below reports it; measuring the geometry of a value that is gone would
                # add a second, less accurate complaint about the same fact.
                continue
            overlay_texts.append(
                OverlayTextFact(
                    pointer=pointer,
                    text=text,
                    style=TEXT_STYLES[overlay.style_id],
                    safe_area=overlay.safe_area,
                )
            )

        # `completed_at` is when the pixels were drawn, and it is what "has this record changed
        # since?" is measured against. A render still without one cannot be compared, so the
        # audit treats every reference as unresolvable rather than as unchanged.
        rendered_at = render.completed_at
        if rendered_at is None:
            audit = VerifiedSourceAudit(
                references=len(references),
                stale=tuple(
                    (f"$.overlays[{index}]", CODE_VERIFIED_VALUE_UNRESOLVABLE)
                    for index, overlay in enumerate(timeline.overlays)
                    if overlay.text_source is not None and overlay.text_source.is_verified
                ),
            )
        else:
            states = await self._facts.verified_record_states(business_id, references, now=now)
            audit = audit_verified_sources(
                [
                    (source.value, reference_id, f"$.overlays[{index}]")
                    for index, overlay in enumerate(timeline.overlays)
                    for source, reference_id in [(overlay.text_source, overlay.reference_id)]
                    if source is not None and source.is_verified and reference_id is not None
                ],
                states,
                rendered_at=rendered_at,
            )

        voiceover_facts = await self._facts.voiceover_drift(business_id, timeline.voiceover_ids)
        return QcFacts(
            profile=render.profile,
            # The renderer concatenates the cut windows, so their sum is what the output should
            # measure. The canvas is an upper bound, not a target.
            expected_duration_ms=sum(
                clip.source_end_ms - clip.source_start_ms for clip in timeline.clips
            ),
            expects_audio=True,
            overlay_texts=tuple(overlay_texts),
            voiceover_drift_ms=voiceover_facts,
            verified=audit,
        )

    async def _settle(
        self,
        business_id: UUID,
        job_id: UUID,
        expected: int,
        claimed: _ClaimedQc,
        *,
        results: tuple[CheckResult, ...],
        measurement: QcMeasurement | None,
        usage: ProviderUsage | None,
        run_failure: str | None,
    ) -> BackgroundJob:
        """Write the report and close the job, whether or not the run got what it came for.

        This runs on the exhausted path too, which is the point: a claimed render always ends up
        with a report. `run_failure` says the *run* could not finish; the verdict says what was
        established about the output. An outage leaves `failed` in one column and `needs_review`
        in the other, and neither can be mistaken for the other.
        """

        decision = decide(results)
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                raise RuntimeError("qc job disappeared")
            attempt = await self._operations.get_active_attempt_for_update(job, expected)
            if attempt is None:
                return job
            report = await self._repository.get_qc_report(business_id, claimed.report_id, lock=True)
            if report is not None:
                report.status = (
                    QcRunStatus.FAILED if run_failure is not None else QcRunStatus.COMPLETED
                )
                report.verdict = decision.verdict
                report.recommended_path = decision.path
                report.checks = serialize_results(results)
                report.measurement = {} if measurement is None else measurement.as_document()
                report.failure_code = run_failure
                report.completed_at = datetime.now(UTC)
                if usage is not None:
                    # Written straight onto the session, matching the voiceover service:
                    # `provider_usage` is the operations module's table but not part of the
                    # job/outbox surface its repository guards.
                    self._session.add(usage)
                    await self._session.flush()
                    report.provider_usage_id = usage.id
            attempt.finished_at = datetime.now(UTC)
            job.finished_at = datetime.now(UTC)
            if run_failure is None:
                attempt.status = JobAttemptStatus.SUCCEEDED
                attempt.error_code = None
                attempt.error_summary = None
                job.status = JobStatus.SUCCEEDED
                job.last_error_code = None
                job.last_error_summary = None
                return job
            # Attempts are exhausted: the attempt and the job record the outage, and the report
            # written above records that the output was never measured. Dead-lettering without
            # the report would leave a render nobody checked and nobody could see was unchecked.
            attempt.status = JobAttemptStatus.FAILED
            attempt.error_code = run_failure
            attempt.error_summary = run_failure
            job.status = JobStatus.DEAD
            job.last_error_code = run_failure
            job.last_error_summary = run_failure
            job.next_attempt_at = None
            return job

    async def _retry(self, business_id: UUID, job_id: UUID, expected: int) -> BackgroundJob | None:
        """Back the job off if attempts remain; return `None` when the caller must settle instead.

        The exhausted branch deliberately leaves the attempt open and touches nothing. `_settle`
        closes both, in the same transaction that writes the report — which is what guarantees
        every render this service claimed ends up with one. An earlier shape marked the attempt
        failed here and then found no active attempt to settle against, leaving the report
        `pending` forever: a render nobody had checked, and no way to tell.
        """

        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                raise RuntimeError("qc job disappeared")
            attempt = await self._operations.get_active_attempt_for_update(job, expected)
            if attempt is None:
                return job
            if job.attempt_count >= job.max_attempts:
                return None
            attempt.status = JobAttemptStatus.FAILED
            attempt.finished_at = datetime.now(UTC)
            attempt.error_code = CODE_MEASUREMENT_UNAVAILABLE
            attempt.error_summary = CODE_MEASUREMENT_UNAVAILABLE
            job.last_error_code = CODE_MEASUREMENT_UNAVAILABLE
            job.last_error_summary = CODE_MEASUREMENT_UNAVAILABLE
            job.finished_at = datetime.now(UTC)
            job.status = JobStatus.FAILED
            job.next_attempt_at = datetime.now(UTC) + timedelta(
                seconds=min(2**job.attempt_count, 60)
            )
            return job

    async def _reload(self, business_id: UUID, job_id: UUID) -> BackgroundJob:
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                raise RuntimeError("qc job disappeared")
            return job

    # --- routing -------------------------------------------------------------------------------

    def _route(self) -> RouteSnapshot:
        """The vision routing decision, persisted with the pending row (ADR-007).

        Written before any call so a run that was billed and never returned still names the
        provider, the model and the ceiling it ran under. `fallbacks` is empty by construction:
        a second opinion on "is this frame inappropriate" is shopping for the answer we want.
        """

        descriptor = self._visual_qc.descriptor
        return RouteSnapshot(
            capability=VISUAL_QC_CAPABILITY,
            provider=descriptor.provider,
            model=descriptor.model,
            route_revision=self._settings.visual_qc_route_revision,
            quality_tier=self._settings.visual_qc_quality_tier,
            timeout_seconds=self._settings.visual_qc_timeout_seconds,
            max_cost_minor=self._settings.visual_qc_max_cost_minor,
            estimated_cost_minor=descriptor.estimated_cost_minor,
            currency=descriptor.currency,
            fallbacks=(),
            data_region=self._settings.visual_qc_data_region,
        )

    def _usage(
        self,
        claimed: _ClaimedQc,
        actual_cost_minor: int,
        started: float,
        *,
        outcome: str,
    ) -> ProviderUsage:
        """One row per external call, written for failures too (§39.1, ADR-007).

        A call that timed out may still have been billed, so the row exists whenever a request
        left this process — and only then. A ceiling that stopped the call before it happened
        writes nothing, because nothing was spent.
        """

        route = self._route()
        return ProviderUsage.from_measurement(
            business_id=claimed.business_id,
            capability=VISUAL_QC_CAPABILITY,
            provider=route.provider,
            model=route.model,
            estimated_cost_minor=route.estimated_cost_minor,
            actual_cost_minor=actual_cost_minor,
            currency=route.currency,
            duration_ms=max(0, round((time.monotonic() - started) * 1000)),
            outcome=outcome,
            correlation_id=claimed.correlation_id,
            asset_id=claimed.render_id,
        )
