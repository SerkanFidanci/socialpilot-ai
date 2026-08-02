"""Tenant-scoped persistence for timelines and renders, plus the reads content borrows.

Every method takes `business_id` and constrains its statement with it; there is no
general-purpose listing. This is the isolation guarantee the validation rules lean on: a clip
that names another tenant's asset produces *no row*, so `TIMELINE_ASSET_NOT_ACCESSIBLE` falls
out of the query rather than out of a comparison somebody has to remember to write.

`ContentFactsReader` is content's read-only window onto media and brands, following the same
pattern `MediaAssetReader` set in the brands module. Content needs three things it does not
own — how long a source is and how big, what the brand forbids, and what a verified record
actually says — and this class is the one named object that coupling flows through.

`claim_next_render_job` is content's own claim query rather than a new method on the operations
repository. Two reasons: `job_type` is a plain string column, so a new durable job type needs
no schema change and no shared enum; and work-order file exclusivity puts
`modules/operations/` outside this slice. The query is the same SKIP LOCKED shape the media
drains use, and the report flags promoting it to a shared helper as follow-up work.

`ScriptFactsReader` (slice 2B) is the second such window, and the reason it is separate from
`ContentFactsReader` is not tidiness: a timeline resolves verified references keyed by PRD
§18.2's dotted `text_source`, while a script resolves them keyed by its own slot kinds. Merging
the two would mean one mapping serving two key spaces, which is how a `verified_product.price`
and a `{{price:…}}` end up silently resolving through each other's rules.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Cursor, apply_cursor, fetch_size
from app.modules.brands.domain import CampaignActivity, evaluate_campaign_activity
from app.modules.brands.models import (
    ApprovedCta,
    BrandAsset,
    BrandAssetRole,
    BrandProfile,
    CampaignOffer,
    ForbiddenClaim,
    Product,
    ProductPrice,
)
from app.modules.content.domain import format_money
from app.modules.content.lifecycle import (
    ProjectState,
    SceneCandidate,
    is_terminal,
    normalize_scene_tag,
)
from app.modules.content.models import (
    ContentProject,
    ContentProjectTransition,
    ContentScript,
    ContentTimeline,
    PromptTemplate,
    RenderOutput,
    RenderQcReport,
    RenderStatus,
    VoiceoverAsset,
)
from app.modules.content.qc import VerifiedRecordState
from app.modules.content.script import (
    BrandBrief,
    CampaignBrief,
    ProductBrief,
    ScenarioCode,
    ScriptStatus,
    SlotKind,
    UntrustedNote,
    format_campaign_end,
    sanitize_untrusted,
)
from app.modules.content.timeline import TextSource
from app.modules.content.tts import VoiceoverStatus
from app.modules.content.validation import AssetFacts, VerifiedValue, VoiceoverFacts
from app.modules.media.models import (
    IngestStatus,
    MediaAsset,
    MediaAssetStatus,
    MediaScene,
    MediaSceneUnderstanding,
    MediaTechnicalMetadata,
    Transcript,
    TranscriptSegment,
)
from app.modules.operations.models import BackgroundJob, JobStatus

RENDER_JOB_TYPE = "content.render"
RENDER_RESOURCE_TYPE = "render_output"
QC_JOB_TYPE = "content.qc"
QC_RESOURCE_TYPE = "render_qc_report"
PROJECT_RESOURCE_TYPE = "content_project"

# Derived from the state machine rather than restated, so the claim predicate, the partial index
# and `lifecycle.is_terminal` cannot drift apart when a state is added.
_TERMINAL_PROJECT_STATES = tuple(state for state in ProjectState if is_terminal(state))


class ContentRepository:
    """Timeline revisions, render records, and the render job claim."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        value: ContentProject
        | ContentProjectTransition
        | ContentScript
        | ContentTimeline
        | RenderOutput
        | RenderQcReport
        | VoiceoverAsset,
    ) -> None:
        self._session.add(value)

    # --- timelines -----------------------------------------------------------------------

    async def get_timeline(
        self, business_id: UUID, timeline_id: UUID, *, lock: bool = False
    ) -> ContentTimeline | None:
        statement = select(ContentTimeline).where(
            ContentTimeline.business_id == business_id, ContentTimeline.id == timeline_id
        )
        if lock:
            statement = statement.with_for_update()
        return cast(ContentTimeline | None, await self._session.scalar(statement))

    async def latest_revision(self, business_id: UUID, root_id: UUID) -> int:
        statement = select(func.max(ContentTimeline.revision)).where(
            ContentTimeline.business_id == business_id, ContentTimeline.root_id == root_id
        )
        return await self._session.scalar(statement) or 0

    async def list_revisions(self, business_id: UUID, root_id: UUID) -> list[ContentTimeline]:
        statement: Select[tuple[ContentTimeline]] = (
            select(ContentTimeline)
            .where(ContentTimeline.business_id == business_id, ContentTimeline.root_id == root_id)
            .order_by(ContentTimeline.revision)
        )
        return list((await self._session.scalars(statement)).all())

    # --- renders -------------------------------------------------------------------------

    async def get_render(
        self, business_id: UUID, render_id: UUID, *, lock: bool = False
    ) -> RenderOutput | None:
        statement = select(RenderOutput).where(
            RenderOutput.business_id == business_id, RenderOutput.id == render_id
        )
        if lock:
            statement = statement.with_for_update()
        return cast(RenderOutput | None, await self._session.scalar(statement))

    async def get_render_for_job(self, business_id: UUID, job_id: UUID) -> RenderOutput | None:
        statement = (
            select(RenderOutput)
            .where(RenderOutput.business_id == business_id, RenderOutput.job_id == job_id)
            .with_for_update()
        )
        return cast(RenderOutput | None, await self._session.scalar(statement))

    async def claim_next_render_job(self) -> BackgroundJob | None:
        """Atomically claim one due render job with SKIP LOCKED.

        Same ordering and due-time predicate as the media drains: queued work first come first
        served, and a failed job only returns once its backoff has elapsed.
        """

        now = datetime.now(UTC)
        statement = (
            select(BackgroundJob)
            .where(
                BackgroundJob.job_type == RENDER_JOB_TYPE,
                BackgroundJob.status.in_((JobStatus.QUEUED, JobStatus.FAILED)),
                (BackgroundJob.status == JobStatus.QUEUED)
                | (
                    BackgroundJob.next_attempt_at.is_not(None)
                    & (BackgroundJob.next_attempt_at <= now)
                ),
            )
            .order_by(BackgroundJob.requested_at, BackgroundJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return cast(BackgroundJob | None, await self._session.scalar(statement))

    # --- quality control -----------------------------------------------------------------

    async def get_qc_report(
        self, business_id: UUID, report_id: UUID, *, lock: bool = False
    ) -> RenderQcReport | None:
        statement = select(RenderQcReport).where(
            RenderQcReport.business_id == business_id, RenderQcReport.id == report_id
        )
        if lock:
            statement = statement.with_for_update()
        return cast(RenderQcReport | None, await self._session.scalar(statement))

    async def latest_qc_report(self, business_id: UUID, render_id: UUID) -> RenderQcReport | None:
        """The newest report for one render. Newest, not only: re-runs are a later slice's."""

        statement: Select[tuple[RenderQcReport]] = (
            select(RenderQcReport)
            .where(
                RenderQcReport.business_id == business_id,
                RenderQcReport.render_id == render_id,
            )
            .order_by(RenderQcReport.created_at.desc(), RenderQcReport.id.desc())
            .limit(1)
        )
        return cast(RenderQcReport | None, await self._session.scalar(statement))

    async def get_qc_report_for_job(self, business_id: UUID, job_id: UUID) -> RenderQcReport | None:
        statement = (
            select(RenderQcReport)
            .where(RenderQcReport.business_id == business_id, RenderQcReport.job_id == job_id)
            .with_for_update()
        )
        return cast(RenderQcReport | None, await self._session.scalar(statement))

    async def claim_next_unchecked_render(self) -> RenderOutput | None:
        """Claim one succeeded render no QC run has opened over yet, with SKIP LOCKED.

        Slice 2D wrote this as an anti-join — "a succeeded render with no report" — and measured
        it at 134 ms per tick over 200k renders, with an index making no difference because
        nothing told the planner that unreported renders are always the newest ones. Slice 2E
        reshapes it as that report asked: `qc_claimed_at` is set on the render row in the same
        transaction that writes the `pending` report, so "awaiting QC" is a predicate on one
        table and `ix_render_outputs_awaiting_qc` is a partial index over a set that is empty in
        steady state.

        The `NOT EXISTS` clause stays as a second, independent statement of the same rule. It is
        not redundancy for its own sake: the column and the report are written together and can
        only diverge through a defect, and after the reshape the anti-join runs over the handful
        of rows the index yielded rather than over the table. `NOT EXISTS` over *any* report —
        not only a completed one — is what keeps automatic QC to one run per render, so a run
        that failed permanently stays dead-lettered instead of being retried forever.
        """

        outstanding = (
            select(RenderQcReport.id).where(RenderQcReport.render_id == RenderOutput.id).exists()
        )
        statement = (
            select(RenderOutput)
            .where(
                RenderOutput.status == RenderStatus.SUCCEEDED,
                RenderOutput.qc_claimed_at.is_(None),
                RenderOutput.master_object_key.is_not(None),
                ~outstanding,
            )
            .order_by(RenderOutput.completed_at, RenderOutput.id)
            .with_for_update(skip_locked=True, of=RenderOutput)
            .limit(1)
        )
        render = cast(RenderOutput | None, await self._session.scalar(statement))
        if render is not None:
            # Stamped here rather than by the caller so the mark and the claim cannot come apart:
            # any future service that claims through this method is claimed *by* this method, and
            # the caller's transaction is the one that makes both durable together.
            render.qc_claimed_at = datetime.now(UTC)
        return render

    async def claim_next_qc_retry(self) -> BackgroundJob | None:
        """Claim one QC job whose transient failure has backed off, with SKIP LOCKED.

        Retries are a separate claim from `claim_next_unchecked_render` because the report row
        already exists for them — the render is no longer "unchecked" from the moment the run
        opened, which is exactly the property that keeps automatic QC to one run per render.
        Draining retries first stops a busy queue of new renders from starving a job that has
        already spent attempts.
        """

        now = datetime.now(UTC)
        statement = (
            select(BackgroundJob)
            .where(
                BackgroundJob.job_type == QC_JOB_TYPE,
                BackgroundJob.status == JobStatus.FAILED,
                BackgroundJob.next_attempt_at.is_not(None),
                BackgroundJob.next_attempt_at <= now,
            )
            .order_by(BackgroundJob.requested_at, BackgroundJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return cast(BackgroundJob | None, await self._session.scalar(statement))

    # --- projects ------------------------------------------------------------------------

    async def get_project(
        self, business_id: UUID, project_id: UUID, *, lock: bool = False
    ) -> ContentProject | None:
        statement = select(ContentProject).where(
            ContentProject.business_id == business_id, ContentProject.id == project_id
        )
        if lock:
            statement = statement.with_for_update()
        return cast(ContentProject | None, await self._session.scalar(statement))

    async def list_projects(
        self,
        business_id: UUID,
        *,
        cursor: Cursor | None,
        limit: int,
        state: ProjectState | None = None,
    ) -> list[ContentProject]:
        """Return at most `limit + 1` rows so the caller can detect a next page."""

        statement: Select[tuple[ContentProject]] = select(ContentProject).where(
            ContentProject.business_id == business_id
        )
        if state is not None:
            statement = statement.where(ContentProject.state == state)
        paged = apply_cursor(
            statement,
            created_at=ContentProject.created_at,
            identifier=ContentProject.id,
            cursor=cursor,
        ).limit(fetch_size(limit))
        return list((await self._session.scalars(paged)).all())

    async def list_transitions(
        self, business_id: UUID, project_id: UUID
    ) -> list[ContentProjectTransition]:
        """One project's whole history, in order. Bounded by the states a project can visit."""

        statement: Select[tuple[ContentProjectTransition]] = (
            select(ContentProjectTransition)
            .where(
                ContentProjectTransition.business_id == business_id,
                ContentProjectTransition.project_id == project_id,
            )
            .order_by(ContentProjectTransition.sequence)
        )
        return list((await self._session.scalars(statement)).all())

    async def next_transition_sequence(self, business_id: UUID, project_id: UUID) -> int:
        statement = select(func.max(ContentProjectTransition.sequence)).where(
            ContentProjectTransition.business_id == business_id,
            ContentProjectTransition.project_id == project_id,
        )
        return (await self._session.scalar(statement) or 0) + 1

    async def claim_next_due_project(self) -> ContentProject | None:
        """Claim one live project whose next check has come due, with SKIP LOCKED.

        The project row is the durable job (see `models.ContentProject`), so this is the claim
        the whole sequencer runs on. The predicate matches `ix_content_projects_due` exactly —
        terminal projects carry no due time and are therefore not merely filtered out but absent
        from the index, which is what keeps the claim independent of how much history a tenant
        has accumulated.
        """

        now = datetime.now(UTC)
        statement = (
            select(ContentProject)
            .where(
                ContentProject.state.notin_(_TERMINAL_PROJECT_STATES),
                ContentProject.next_check_at.is_not(None),
                ContentProject.next_check_at <= now,
            )
            .order_by(ContentProject.next_check_at, ContentProject.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return cast(ContentProject | None, await self._session.scalar(statement))

    # --- abandoned provider runs -----------------------------------------------------------

    async def claim_stale_pending_scripts(
        self, *, older_than: datetime, limit: int
    ) -> list[ContentScript]:
        """Script rows that opened before `older_than` and never settled, locked for update.

        A `pending` row means a provider call may have been billed and never returned — slice 2B
        writes it before the call precisely so that is visible. What it must not do is stay
        visible forever: an abandoned row keeps a script that will never exist in a state that
        reads like one still being produced, and slice 2E's sequencer would wait on it.
        """

        statement: Select[tuple[ContentScript]] = (
            select(ContentScript)
            .where(
                ContentScript.status == ScriptStatus.PENDING,
                ContentScript.created_at < older_than,
            )
            .order_by(ContentScript.created_at, ContentScript.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def claim_stale_pending_voiceovers(
        self, *, older_than: datetime, limit: int
    ) -> list[VoiceoverAsset]:
        """The same sweep for speech runs, which spend several calls behind one row."""

        statement: Select[tuple[VoiceoverAsset]] = (
            select(VoiceoverAsset)
            .where(
                VoiceoverAsset.status == VoiceoverStatus.PENDING,
                VoiceoverAsset.created_at < older_than,
            )
            .order_by(VoiceoverAsset.created_at, VoiceoverAsset.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    # --- scripts -------------------------------------------------------------------------

    async def get_script(
        self, business_id: UUID, script_id: UUID, *, lock: bool = False
    ) -> ContentScript | None:
        statement = select(ContentScript).where(
            ContentScript.business_id == business_id, ContentScript.id == script_id
        )
        if lock:
            statement = statement.with_for_update()
        return cast(ContentScript | None, await self._session.scalar(statement))

    async def list_scripts(
        self,
        business_id: UUID,
        *,
        cursor: Cursor | None,
        limit: int,
        scenario_code: ScenarioCode | None = None,
        status: ScriptStatus | None = None,
    ) -> list[ContentScript]:
        """Return at most `limit + 1` rows so the caller can detect a next page."""

        statement: Select[tuple[ContentScript]] = select(ContentScript).where(
            ContentScript.business_id == business_id
        )
        if scenario_code is not None:
            statement = statement.where(ContentScript.scenario_code == scenario_code)
        if status is not None:
            statement = statement.where(ContentScript.status == status)
        paged = apply_cursor(
            statement,
            created_at=ContentScript.created_at,
            identifier=ContentScript.id,
            cursor=cursor,
        ).limit(fetch_size(limit))
        return list((await self._session.scalars(paged)).all())

    # --- voiceovers ----------------------------------------------------------------------

    async def get_voiceover(
        self, business_id: UUID, voiceover_id: UUID, *, lock: bool = False
    ) -> VoiceoverAsset | None:
        statement = select(VoiceoverAsset).where(
            VoiceoverAsset.business_id == business_id, VoiceoverAsset.id == voiceover_id
        )
        if lock:
            statement = statement.with_for_update()
        return cast(VoiceoverAsset | None, await self._session.scalar(statement))

    async def list_voiceovers(
        self,
        business_id: UUID,
        *,
        cursor: Cursor | None,
        limit: int,
        script_id: UUID | None = None,
        status: VoiceoverStatus | None = None,
    ) -> list[VoiceoverAsset]:
        """Return at most `limit + 1` rows so the caller can detect a next page."""

        statement: Select[tuple[VoiceoverAsset]] = select(VoiceoverAsset).where(
            VoiceoverAsset.business_id == business_id
        )
        if script_id is not None:
            statement = statement.where(VoiceoverAsset.script_id == script_id)
        if status is not None:
            statement = statement.where(VoiceoverAsset.status == status)
        paged = apply_cursor(
            statement,
            created_at=VoiceoverAsset.created_at,
            identifier=VoiceoverAsset.id,
            cursor=cursor,
        ).limit(fetch_size(limit))
        return list((await self._session.scalars(paged)).all())

    async def active_prompt_template(self, code: str) -> PromptTemplate | None:
        """The one live version of a prompt. No tenant filter: §17.6 is platform configuration.

        A generation with no active template is refused rather than run with a hard-coded
        fallback, because "which prompt produced this" is a question the record has to be able
        to answer for every script that exists.
        """

        statement = select(PromptTemplate).where(
            PromptTemplate.code == code, PromptTemplate.active.is_(True)
        )
        return cast(PromptTemplate | None, await self._session.scalar(statement))


class ContentFactsReader:
    """Content's read-only window onto media and brand records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def asset_facts(
        self, business_id: UUID, asset_ids: Sequence[UUID]
    ) -> dict[UUID, AssetFacts]:
        """Return facts for the tenant's own assets only; unknown ids are simply absent."""

        if not asset_ids:
            return {}
        statement = (
            select(MediaAsset, MediaTechnicalMetadata)
            .outerjoin(
                MediaTechnicalMetadata,
                MediaTechnicalMetadata.asset_id == MediaAsset.id,
            )
            .where(MediaAsset.business_id == business_id, MediaAsset.id.in_(tuple(asset_ids)))
        )
        facts: dict[UUID, AssetFacts] = {}
        for asset, metadata in (await self._session.execute(statement)).all():
            # "Renderable" is deliberately strict: the upload finished, the ingest gate passed,
            # and technical analysis produced dimensions. A quarantined or half-analyzed asset
            # is refused at validation instead of failing inside FFmpeg.
            renderable = (
                asset.status == MediaAssetStatus.UPLOADED
                and asset.ingest_status == IngestStatus.READY_FOR_ANALYSIS
                and metadata is not None
            )
            facts[asset.id] = AssetFacts(
                asset_id=asset.id,
                duration_ms=metadata.duration_ms if metadata is not None else None,
                width=metadata.width if metadata is not None else None,
                height=metadata.height if metadata is not None else None,
                has_audio=bool(metadata is not None and metadata.has_audio),
                renderable=renderable,
                source_object_key=asset.storage_object_key,
            )
        return facts

    async def voiceover_facts(
        self, business_id: UUID, voiceover_ids: Sequence[UUID]
    ) -> dict[UUID, VoiceoverFacts]:
        """Facts for the tenant's own voiceovers; unknown ids are simply absent.

        A `voiceover` audio track names a `voiceover_assets` row, not a `media_assets` row —
        the audio was produced by this pipeline, never uploaded — so it resolves through its own
        query. Absence covers "does not exist" and "belongs to another tenant" with one rule,
        the same way asset facts do.
        """

        if not voiceover_ids:
            return {}
        statement = select(VoiceoverAsset).where(
            VoiceoverAsset.business_id == business_id,
            VoiceoverAsset.id.in_(tuple(voiceover_ids)),
        )
        return {
            row.id: VoiceoverFacts(
                voiceover_id=row.id,
                # Only a settled run may be placed on a timeline: a `pending` row means calls
                # may still be in flight, and a `failed` one has at best partial audio.
                usable=row.status is VoiceoverStatus.GENERATED,
                # The ffprobe-measured total. `None` would mean nothing measured it, which is
                # exactly the case §18.3's duration check must refuse rather than skip.
                duration_ms=row.total_duration_ms,
            )
            for row in (await self._session.scalars(statement)).all()
        }

    async def voiceover_drift(self, business_id: UUID, voiceover_ids: Sequence[UUID]) -> int | None:
        """Total measured drift across the timeline's voiceovers, or `None` when it places none.

        Slice 2C stored `drift_ms` as a real column and deliberately refused to judge it; this is
        the read that lets slice 2D do so. `None` means the document has no voiceover track — a
        fact about the timeline, not a measurement that failed — which is why the check it feeds
        passes with `applicable: false` rather than going `unknown`.

        A voiceover row whose drift is `NULL` is a run that never settled, and that *is* an
        unmeasured value; it contributes nothing to the sum but leaves the sum defined only when
        at least one row carries a number.
        """

        if not voiceover_ids:
            return None
        statement = select(VoiceoverAsset.drift_ms).where(
            VoiceoverAsset.business_id == business_id,
            VoiceoverAsset.id.in_(tuple(voiceover_ids)),
        )
        measured = [
            value for value in (await self._session.scalars(statement)).all() if value is not None
        ]
        return sum(measured) if measured else None

    async def voiceover_object_keys(
        self, business_id: UUID, voiceover_ids: Sequence[UUID]
    ) -> dict[UUID, tuple[str, ...]]:
        """Each voiceover's stored audio objects, in line order, tenant-scoped.

        Only a `generated` run yields keys. A `pending` or `failed` row may own objects on
        storage — slice 2C records partial runs deliberately — but those are evidence of what was
        billed, not audio anything may lay over a frame, and returning them here would let the
        renderer mix half a script.
        """

        if not voiceover_ids:
            return {}
        statement = select(VoiceoverAsset).where(
            VoiceoverAsset.business_id == business_id,
            VoiceoverAsset.id.in_(tuple(voiceover_ids)),
            VoiceoverAsset.status == VoiceoverStatus.GENERATED,
        )
        keys: dict[UUID, tuple[str, ...]] = {}
        for row in (await self._session.scalars(statement)).all():
            ordered = sorted(
                (segment for segment in row.segments or []),
                key=lambda segment: int(cast(int, segment.get("index", 0))),
            )
            keys[row.id] = tuple(
                str(segment["object_key"]) for segment in ordered if segment.get("object_key")
            )
        return keys

    async def scene_candidates(
        self, business_id: UUID, asset_ids: Sequence[UUID], *, limit: int
    ) -> tuple[SceneCandidate, ...]:
        """Detected scenes with whatever video understanding labelled them, in asset order.

        The tags are `labels + objects + actions` from the understanding row, put through
        `lifecycle.normalize_scene_tag` — the one definition of how a scene tag is spelled, so
        both sides of the comparison are normalized identically and neither is folded to ASCII.

        A scene with no understanding row still comes back, with no tags. It is usable footage;
        it simply cannot be selected *because of* what is in it.
        """

        if not asset_ids:
            return ()
        order = {asset_id: index for index, asset_id in enumerate(asset_ids)}
        statement = (
            select(MediaScene, MediaSceneUnderstanding)
            .outerjoin(
                MediaSceneUnderstanding,
                MediaSceneUnderstanding.scene_id == MediaScene.id,
            )
            .where(MediaScene.business_id == business_id, MediaScene.asset_id.in_(tuple(asset_ids)))
            .order_by(MediaScene.asset_id, MediaScene.scene_index)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        candidates = [
            SceneCandidate(
                asset_id=scene.asset_id,
                start_ms=scene.start_ms,
                end_ms=scene.end_ms,
                tags=_scene_candidate_tags(understanding),
            )
            for scene, understanding in rows
        ]
        # The caller passed assets in a meaningful order (the project's own list); the query can
        # only order by id, so the intended order is restored here rather than left to chance.
        candidates.sort(key=lambda item: (order.get(item.asset_id, len(order)), item.start_ms))
        return tuple(candidates)

    async def logo_asset_ids(self, business_id: UUID) -> frozenset[UUID]:
        statement = select(BrandAsset.media_asset_id).where(
            BrandAsset.business_id == business_id,
            BrandAsset.role.in_((BrandAssetRole.LOGO, BrandAssetRole.LOGO_ALTERNATE)),
        )
        return frozenset((await self._session.scalars(statement)).all())

    async def forbidden_terms(self, business_id: UUID) -> tuple[str, ...]:
        """The brand's forbidden claims and topics, as one list of terms to match against."""

        claims = list(
            (
                await self._session.scalars(
                    select(ForbiddenClaim.value).where(ForbiddenClaim.business_id == business_id)
                )
            ).all()
        )
        topics = await self._session.scalar(
            select(BrandProfile.forbidden_topics).where(BrandProfile.business_id == business_id)
        )
        return tuple(claims) + tuple(topics or ())

    async def scene_boundaries(
        self, business_id: UUID, asset_ids: Sequence[UUID]
    ) -> dict[UUID, tuple[int, ...]]:
        """Detected scene cut points per asset — the grid parametric edits snap to."""

        if not asset_ids:
            return {}
        statement = (
            select(MediaScene.asset_id, MediaScene.start_ms, MediaScene.end_ms)
            .where(MediaScene.business_id == business_id, MediaScene.asset_id.in_(tuple(asset_ids)))
            .order_by(MediaScene.asset_id, MediaScene.scene_index)
        )
        boundaries: dict[UUID, set[int]] = {}
        for asset_id, start_ms, end_ms in (await self._session.execute(statement)).all():
            boundaries.setdefault(asset_id, set()).update((start_ms, end_ms))
        return {asset_id: tuple(sorted(points)) for asset_id, points in boundaries.items()}

    async def transcript_segments(
        self, business_id: UUID, asset_id: UUID
    ) -> tuple[tuple[int, int, str], ...]:
        """`(start_ms, end_ms, text)` for one asset's stored transcript.

        Reading stored transcript rows is not an AI call: the text was produced upstream and
        persisted. Captions in this slice are a projection of that record onto the cut, which
        is why a render can burn subtitles without a provider anywhere in the path.
        """

        statement = (
            select(TranscriptSegment.start_ms, TranscriptSegment.end_ms, TranscriptSegment.text)
            .join(Transcript, Transcript.id == TranscriptSegment.transcript_id)
            .where(Transcript.business_id == business_id, Transcript.asset_id == asset_id)
            .order_by(TranscriptSegment.segment_index)
        )
        return tuple(
            (start_ms, end_ms, text)
            for start_ms, end_ms, text in (await self._session.execute(statement)).all()
        )

    async def verified_values(
        self,
        business_id: UUID,
        references: Sequence[tuple[TextSource, UUID]],
        *,
        now: datetime,
    ) -> dict[tuple[str, UUID], VerifiedValue]:
        """Resolve every verified reference the timeline makes, tenant-scoped.

        A reference that resolves to nothing is simply left out of the mapping, and validation
        turns that absence into `TIMELINE_VERIFIED_FIELD_NOT_FOUND`. That covers a made-up id,
        another tenant's record, and a reference pointed at the wrong kind of record with one
        rule instead of three.
        """

        resolved: dict[tuple[str, UUID], VerifiedValue] = {}
        wanted: dict[TextSource, set[UUID]] = {}
        for source, reference_id in references:
            wanted.setdefault(source, set()).add(reference_id)

        campaign_ids = wanted.get(TextSource.VERIFIED_CAMPAIGN_TITLE, set()) | wanted.get(
            TextSource.VERIFIED_CAMPAIGN_LEGAL_TEXT, set()
        )
        if campaign_ids:
            offers = await self._session.scalars(
                select(CampaignOffer).where(
                    CampaignOffer.business_id == business_id,
                    CampaignOffer.id.in_(tuple(campaign_ids)),
                )
            )
            for offer in offers:
                active = (
                    evaluate_campaign_activity(
                        status=offer.status,
                        approval_status=offer.approval_status,
                        starts_at=offer.starts_at,
                        ends_at=offer.ends_at,
                        now=now,
                    )
                    is CampaignActivity.ACTIVE
                )
                resolved[(TextSource.VERIFIED_CAMPAIGN_TITLE.value, offer.id)] = VerifiedValue(
                    text=offer.name, within_window=active
                )
                if offer.legal_text:
                    resolved[(TextSource.VERIFIED_CAMPAIGN_LEGAL_TEXT.value, offer.id)] = (
                        VerifiedValue(text=offer.legal_text, within_window=active)
                    )

        product_ids = wanted.get(TextSource.VERIFIED_PRODUCT_PRICE, set())
        if product_ids:
            prices = await self._session.scalars(
                select(ProductPrice).where(
                    ProductPrice.business_id == business_id,
                    ProductPrice.product_id.in_(tuple(product_ids)),
                    # Only the open price row. A superseded price must never reach a frame.
                    ProductPrice.effective_to.is_(None),
                )
            )
            for price in prices:
                resolved[(TextSource.VERIFIED_PRODUCT_PRICE.value, price.product_id)] = (
                    VerifiedValue(
                        text=format_money(amount_minor=price.price_minor, currency=price.currency),
                        within_window=True,
                    )
                )

        cta_ids = wanted.get(TextSource.VERIFIED_CTA_TEXT, set())
        if cta_ids:
            ctas = await self._session.scalars(
                select(ApprovedCta).where(
                    ApprovedCta.business_id == business_id, ApprovedCta.id.in_(tuple(cta_ids))
                )
            )
            for cta in ctas:
                resolved[(TextSource.VERIFIED_CTA_TEXT.value, cta.id)] = VerifiedValue(
                    text=cta.value, within_window=True
                )
        return resolved

    async def verified_record_states(
        self,
        business_id: UUID,
        references: Sequence[tuple[TextSource, UUID]],
        *,
        now: datetime,
    ) -> dict[tuple[str, UUID], VerifiedRecordState]:
        """Read each referenced record's existence, window and last change, tenant-scoped.

        This is the raw material for §19.4's "fiyat ve tarih kaynağa uyuyor mu", kept separate
        from `verified_values` on purpose: that method resolves a value *to draw*, this one reads
        history *to judge*, and a QC path that resolved values would end up holding a price it
        has no business holding.

        `changed_at` is when the current value became current. `product_prices` is append-only,
        so the open row's `effective_from` answers it exactly — a price row that opened after a
        render finished means the render printed the row that has since been closed.
        `campaign_offers` is edited in place and carries `updated_at`. `approved_ctas` carries
        neither history nor an update timestamp, so `changed_at` is `None` there and an edited
        CTA is a gap this check cannot see; only its disappearance is detectable. Recording that
        as `None` rather than as "unchanged" is what keeps the limit visible instead of implied.
        """

        states: dict[tuple[str, UUID], VerifiedRecordState] = {}
        wanted: dict[TextSource, set[UUID]] = {}
        for source, reference_id in references:
            wanted.setdefault(source, set()).add(reference_id)

        campaign_sources = (
            TextSource.VERIFIED_CAMPAIGN_TITLE,
            TextSource.VERIFIED_CAMPAIGN_LEGAL_TEXT,
        )
        campaign_ids = {
            reference_id for source in campaign_sources for reference_id in wanted.get(source, ())
        }
        if campaign_ids:
            offers = await self._session.scalars(
                select(CampaignOffer).where(
                    CampaignOffer.business_id == business_id,
                    CampaignOffer.id.in_(tuple(campaign_ids)),
                )
            )
            found = {offer.id: offer for offer in offers}
            for source in campaign_sources:
                for reference_id in wanted.get(source, ()):
                    offer = found.get(reference_id)
                    if offer is None:
                        states[(source.value, reference_id)] = VerifiedRecordState(
                            exists=False, within_window=False, changed_at=None
                        )
                        continue
                    states[(source.value, reference_id)] = VerifiedRecordState(
                        exists=True,
                        within_window=evaluate_campaign_activity(
                            status=offer.status,
                            approval_status=offer.approval_status,
                            starts_at=offer.starts_at,
                            ends_at=offer.ends_at,
                            now=now,
                        )
                        is CampaignActivity.ACTIVE,
                        changed_at=offer.updated_at,
                    )

        for reference_id in wanted.get(TextSource.VERIFIED_PRODUCT_PRICE, ()):
            price = cast(
                ProductPrice | None,
                await self._session.scalar(
                    select(ProductPrice).where(
                        ProductPrice.business_id == business_id,
                        ProductPrice.product_id == reference_id,
                        ProductPrice.effective_to.is_(None),
                    )
                ),
            )
            states[(TextSource.VERIFIED_PRODUCT_PRICE.value, reference_id)] = VerifiedRecordState(
                exists=price is not None,
                within_window=price is not None,
                changed_at=None if price is None else price.effective_from,
            )

        for reference_id in wanted.get(TextSource.VERIFIED_CTA_TEXT, ()):
            cta = await self._session.scalar(
                select(ApprovedCta.id).where(
                    ApprovedCta.business_id == business_id, ApprovedCta.id == reference_id
                )
            )
            states[(TextSource.VERIFIED_CTA_TEXT.value, reference_id)] = VerifiedRecordState(
                exists=cta is not None, within_window=cta is not None, changed_at=None
            )
        return states


class ScriptFactsReader:
    """Script generation's read-only window onto brands and media.

    Every method takes `business_id` and constrains its statement with it, so another tenant's
    product, campaign, CTA or asset produces *no row*. The service turns that absence into a
    `404` for a declared input and into `SCRIPT_VERIFIED_FIELD_NOT_FOUND` for a slot the model
    emitted — one query shape, two honest answers, no cross-tenant comparison to forget.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def brand_brief(self, business_id: UUID) -> BrandBrief | None:
        statement = select(BrandProfile).where(BrandProfile.business_id == business_id)
        profile = cast(BrandProfile | None, await self._session.scalar(statement))
        if profile is None:
            return None
        return BrandBrief(
            name=profile.display_name,
            tone=profile.tone,
            language=profile.communication_language,
        )

    async def product_brief(self, business_id: UUID, product_id: UUID) -> ProductBrief | None:
        statement = select(Product).where(
            Product.business_id == business_id, Product.id == product_id
        )
        product = cast(Product | None, await self._session.scalar(statement))
        if product is None:
            return None
        return ProductBrief(
            product_id=product.id,
            name=product.name,
            category=product.category,
            description=product.description,
        )

    async def campaign_brief(
        self, business_id: UUID, campaign_id: UUID, *, now: datetime
    ) -> CampaignBrief | None:
        statement = select(CampaignOffer).where(
            CampaignOffer.business_id == business_id, CampaignOffer.id == campaign_id
        )
        offer = cast(CampaignOffer | None, await self._session.scalar(statement))
        if offer is None:
            return None
        return CampaignBrief(
            campaign_id=offer.id,
            name=offer.name,
            ends_at=offer.ends_at,
            # W04's deterministic verdict, reused rather than re-derived. A second definition of
            # "active" is a second answer waiting to disagree with the first.
            active=evaluate_campaign_activity(
                status=offer.status,
                approval_status=offer.approval_status,
                starts_at=offer.starts_at,
                ends_at=offer.ends_at,
                now=now,
            )
            is CampaignActivity.ACTIVE,
        )

    async def cta_text(self, business_id: UUID, cta_id: UUID) -> str | None:
        statement = select(ApprovedCta.value).where(
            ApprovedCta.business_id == business_id, ApprovedCta.id == cta_id
        )
        return cast(str | None, await self._session.scalar(statement))

    async def current_price(self, business_id: UUID, product_id: UUID) -> str | None:
        """The open price row, formatted. A superseded price can never reach a script."""

        statement = select(ProductPrice).where(
            ProductPrice.business_id == business_id,
            ProductPrice.product_id == product_id,
            ProductPrice.effective_to.is_(None),
        )
        price = cast(ProductPrice | None, await self._session.scalar(statement))
        if price is None:
            return None
        return format_money(amount_minor=price.price_minor, currency=price.currency)

    async def known_asset_ids(
        self, business_id: UUID, asset_ids: Sequence[UUID]
    ) -> frozenset[UUID]:
        if not asset_ids:
            return frozenset()
        statement = select(MediaAsset.id).where(
            MediaAsset.business_id == business_id, MediaAsset.id.in_(tuple(asset_ids))
        )
        return frozenset((await self._session.scalars(statement)).all())

    async def media_notes(
        self,
        business_id: UUID,
        asset_ids: Sequence[UUID],
        *,
        max_notes: int,
        max_chars: int,
    ) -> tuple[UntrustedNote, ...]:
        """Transcript lines and scene descriptions for the named assets, sanitized.

        This text came out of a customer's video. It is the single most likely place for an
        injection attempt to enter the pipeline (ADR-006, §17.5), so it is flattened to one
        bounded line each here and handed to the prompt builder as *data*. Nothing downstream
        reads it as an instruction, and nothing acts on what it says.
        """

        if not asset_ids or max_notes < 1:
            return ()
        ids = tuple(asset_ids)
        notes: list[UntrustedNote] = []

        transcripts = (
            select(Transcript.asset_id, TranscriptSegment.text)
            .join(TranscriptSegment, TranscriptSegment.transcript_id == Transcript.id)
            .where(Transcript.business_id == business_id, Transcript.asset_id.in_(ids))
            .order_by(Transcript.asset_id, TranscriptSegment.segment_index)
            .limit(max_notes)
        )
        for asset_id, text_value in (await self._session.execute(transcripts)).all():
            notes.append(
                UntrustedNote(
                    source="transcript",
                    asset_id=asset_id,
                    text=sanitize_untrusted(text_value, max_chars=max_chars),
                )
            )

        remaining = max_notes - len(notes)
        if remaining > 0:
            scenes = (
                select(MediaSceneUnderstanding.asset_id, MediaSceneUnderstanding.summary)
                .where(
                    MediaSceneUnderstanding.business_id == business_id,
                    MediaSceneUnderstanding.asset_id.in_(ids),
                )
                .order_by(MediaSceneUnderstanding.asset_id, MediaSceneUnderstanding.id)
                .limit(remaining)
            )
            for asset_id, summary in (await self._session.execute(scenes)).all():
                notes.append(
                    UntrustedNote(
                        source="scene_summary",
                        asset_id=asset_id,
                        text=sanitize_untrusted(summary, max_chars=max_chars),
                    )
                )
        return tuple(note for note in notes if note.text)

    async def slot_values(
        self,
        business_id: UUID,
        *,
        product_id: UUID | None,
        campaign_offer_id: UUID | None,
        cta_id: UUID | None,
        timezone_name: str,
        now: datetime,
    ) -> dict[tuple[str, UUID], VerifiedValue]:
        """Resolve exactly the slots the *request* declared, and nothing else.

        Only the inputs the caller named are resolvable. A model that emits a slot pointing at
        some other record — another tenant's product, a campaign nobody asked for — finds no
        entry here, so it is rejected by the same rule that catches an invented id. The tenant
        filter below is the second gate, not the only one.
        """

        values: dict[tuple[str, UUID], VerifiedValue] = {}
        if product_id is not None:
            price = await self.current_price(business_id, product_id)
            if price is not None:
                values[(SlotKind.PRICE.value, product_id)] = VerifiedValue(
                    text=price, within_window=True
                )
        if campaign_offer_id is not None:
            campaign = await self.campaign_brief(business_id, campaign_offer_id, now=now)
            if campaign is not None:
                values[(SlotKind.CAMPAIGN_TITLE.value, campaign_offer_id)] = VerifiedValue(
                    text=campaign.name, within_window=campaign.active
                )
                values[(SlotKind.CAMPAIGN_END.value, campaign_offer_id)] = VerifiedValue(
                    text=format_campaign_end(campaign.ends_at, timezone_name=timezone_name),
                    within_window=campaign.active,
                )
        if cta_id is not None:
            cta = await self.cta_text(business_id, cta_id)
            if cta is not None:
                values[(SlotKind.CTA.value, cta_id)] = VerifiedValue(text=cta, within_window=True)
        return values


def _scene_candidate_tags(understanding: MediaSceneUnderstanding | None) -> frozenset[str]:
    """Flatten one understanding row into the tag vocabulary scene selection matches on."""

    if understanding is None:
        return frozenset()
    values = (
        *(understanding.labels or ()),
        *(understanding.objects or ()),
        *(understanding.actions or ()),
    )
    return frozenset(tag for value in values if (tag := normalize_scene_tag(str(value))))


def references_in(
    pairs: Sequence[tuple[TextSource, UUID]],
) -> Mapping[TextSource, frozenset[UUID]]:
    """Group reference pairs by source; used by callers assembling a validation context."""

    grouped: dict[TextSource, set[UUID]] = {}
    for source, reference_id in pairs:
        grouped.setdefault(source, set()).add(reference_id)
    return {source: frozenset(ids) for source, ids in grouped.items()}
