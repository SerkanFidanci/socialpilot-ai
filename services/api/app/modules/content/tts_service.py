"""Voiceover production: authorization, routing, the paid calls, measurement, storage.

The shape mirrors script generation — **two transactions with provider calls between them** — and
the reasons are the same. The first transaction commits a `pending` row carrying the route
snapshot (ADR-007) *before* anything is called; the second settles it with one `provider_usage`
row per call, the stored objects, and the measured durations. Holding a transaction open across
the calls would pin a PostgreSQL connection and snapshot for the length of several network round
trips, and a crash mid-run would roll back the only record that billable calls ever happened.

Three things are specific to speech.

**Nothing is spoken that a record did not vouch for.** The request names a `content_scripts` row
and a voice; there is no field for text. What gets synthesized is the script's *resolved*
document, so a price a listener hears is a price `product_prices` held. Slice 2B put three
independent mechanisms behind that guarantee; this slice adds none of its own and needs none,
because it never accepts prose from anywhere else.

**Duration is measured, not believed.** Every file is probed with ffprobe after it is written.
The provider's own claim is recorded beside the measurement so a disagreement stays visible, but
nothing downstream reads it — §18.3's "seslendirme süresi" check, the drift record slice 2D will
consume, and the totals on the row all come from the probe.

**A run is several calls, and a partial run is a real state.** Lines are synthesized in order and
a failure on the third one leaves two objects already in storage. Those two are written into the
`failed` row rather than forgotten, so the bytes are attributable instead of orphaned, and every
call that happened gets its usage row whether the run succeeded or not.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.core.pagination import Cursor, Page, build_page, resolve_limit
from app.modules.businesses.models import BusinessStatus
from app.modules.businesses.repository import BusinessRepository
from app.modules.content.models import ContentScript, VoiceoverAsset
from app.modules.content.policy import ContentAction, permits_action
from app.modules.content.repository import ContentRepository
from app.modules.content.script import RouteSnapshot, ScriptStatus
from app.modules.content.tts import (
    MAX_SEGMENT_AUDIO_MS,
    MAX_TOTAL_AUDIO_MS,
    MIN_SEGMENT_AUDIO_MS,
    TTS_CAPABILITY,
    AudioFormat,
    AudioProbePermanentError,
    AudioProbePort,
    AudioProbeTransientError,
    AudioResult,
    ProviderDescriptor,
    SynthesisRequest,
    TTSDisabledError,
    TTSPermanentError,
    TTSPort,
    TTSTransientError,
    VoiceoverLine,
    VoiceoverSegment,
    VoiceoverSourceError,
    VoiceoverStatus,
    VoiceProfile,
    resolve_voice_profile,
    script_lines,
    segment_object_key,
    serialize_segments,
    total_drift_ms,
    total_duration_ms,
)
from app.modules.media.storage import (
    MultipartStoragePort,
    StoragePermanentError,
    StorageUnavailableError,
)
from app.modules.operations.models import AuditLog, IdempotencyKey, ProviderUsage
from app.modules.operations.repository import OperationsRepository
from app.modules.operations.service import (
    IdempotencyService,
    OperationsService,
    request_fingerprint,
)

# The container every voiceover object is written in. A setting would imply the choice is
# deployment-specific; it is not — storage, the probe and any future mixing filter all have to
# agree on it, so it changes as a code change with a migration of existing objects or not at all.
VOICEOVER_AUDIO_FORMAT = AudioFormat.WAV

_OUTCOME_SUCCEEDED = "succeeded"
_OUTCOME_FAILED = "failed"
_OUTCOME_REJECTED = "rejected"
_OUTCOME_OVER_BUDGET = "over_budget"


@dataclass(frozen=True, slots=True)
class VoiceoverRequest:
    """What the caller asks for: a script and a voice. There is no third field, on purpose."""

    script_id: UUID
    voice_profile_code: str | None

    def as_payload(self) -> dict[str, object]:
        """The canonical form the idempotency fingerprint is taken over.

        The whole request, not a summary of it. A fingerprint over anything less lets the same
        key with a different voice replay the first run's audio while the caller believes the
        second one landed — the failure W11 shipped and W14 closed, in a new place.
        """

        return {
            "script_id": str(self.script_id),
            "voice_profile_code": self.voice_profile_code,
        }


@dataclass(frozen=True, slots=True)
class _CallRecord:
    """One synthesis call, as `provider_usage` will record it."""

    estimated_cost_minor: int
    actual_cost_minor: int
    currency: str
    duration_ms: int
    outcome: str


@dataclass(frozen=True, slots=True)
class _Prepared:
    """The committed intent to call a provider, plus everything the calls need."""

    voiceover_id: UUID
    lines: tuple[VoiceoverLine, ...]
    profile: VoiceProfile
    snapshot: RouteSnapshot
    idempotency: _IdempotentRequest | None


@dataclass(frozen=True, slots=True)
class _Produced:
    """Everything the settling transaction has to write, success or failure."""

    segments: tuple[VoiceoverSegment, ...]
    calls: tuple[_CallRecord, ...]
    problem: ProblemException | None


@dataclass(frozen=True, slots=True)
class _Replay:
    voiceover: VoiceoverAsset


class VoiceoverService:
    """PRD §14.8's voiceover step, produced under §17.4 routing and §17.3's provider shape."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        tts: TTSPort,
        probe: AudioProbePort,
        storage: MultipartStoragePort,
    ) -> None:
        self._session = session
        self._settings = settings
        self._tts = tts
        self._probe = probe
        self._storage = storage
        self._repository = ContentRepository(session)
        self._businesses = BusinessRepository(session)

    # --- production -------------------------------------------------------------------------

    async def generate(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        request: VoiceoverRequest,
        idempotency_key: str | None,
        correlation_id: str,
    ) -> VoiceoverAsset:
        prepared = await self._prepare(
            user_id=user_id,
            business_id=business_id,
            request=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        if isinstance(prepared, _Replay):
            return prepared.voiceover

        # One directory for the whole run: every intermediate file goes away with it, success or
        # failure, so nothing accumulates in the scratch budget of ADR-013's single server.
        with TemporaryDirectory(prefix="voiceover-") as temporary:
            produced = await self._produce(
                business_id=business_id, prepared=prepared, workdir=Path(temporary)
            )
        return await self._settle(
            prepared,
            business_id=business_id,
            user_id=user_id,
            produced=produced,
            correlation_id=correlation_id,
        )

    async def _prepare(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        request: VoiceoverRequest,
        idempotency_key: str | None,
        correlation_id: str,
    ) -> _Prepared | _Replay:
        """Authorize, resolve the script and the voice, and commit the intent to call.

        The disabled adapter and the per-call ceiling are checked *before* the transaction, so a
        refused run leaves no trace of an attempt that never happened.
        """

        descriptor = self._tts.descriptor
        if not descriptor.enabled:
            raise self._not_configured()
        ceiling = self._settings.tts_max_cost_minor
        if descriptor.estimated_cost_minor > ceiling:
            raise self._cost_limit_problem()

        async with self._session.begin():
            await self._authorize(user_id, business_id, ContentAction.VOICEOVER_GENERATE)
            await self._require_active_business(business_id)
            replay = await self._begin_idempotent(
                business_id=business_id,
                user_id=user_id,
                key=idempotency_key,
                payload=request.as_payload(),
                correlation_id=correlation_id,
            )
            if replay is not None and replay.voiceover_id is not None:
                return _Replay(voiceover=await self._load(business_id, replay.voiceover_id))

            profile = self._resolve_profile(request.voice_profile_code)
            script = await self._require_voiceable_script(business_id, request.script_id)
            lines = self._require_lines(script)
            # The whole run's estimate, not one call's: eight lines at a per-call estimate under
            # the ceiling can still be a bill nobody authorized.
            estimated = descriptor.estimated_cost_minor * len(lines)
            if estimated > ceiling:
                raise self._cost_limit_problem()

            snapshot = self._route_snapshot(
                descriptor=descriptor, ceiling=ceiling, estimated_cost_minor=estimated
            )
            record = VoiceoverAsset(
                id=uuid4(),
                business_id=business_id,
                script_id=script.id,
                status=VoiceoverStatus.PENDING,
                voice_profile_code=profile.code,
                voice_profile_version=profile.version,
                voice_profile=profile.as_document(),
                audio_format=VOICEOVER_AUDIO_FORMAT.value,
                segments=[],
                total_duration_ms=None,
                target_duration_ms=sum(line.target_duration_ms for line in lines),
                drift_ms=None,
                route_snapshot=snapshot.as_document(),
                provider_usage_id=None,
                failure_code=None,
                requested_by_user_id=user_id,
                correlation_id=correlation_id,
            )
            self._repository.add(record)
            await self._session.flush()
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="content.voiceover.requested",
                resource_id=record.id,
                correlation_id=correlation_id,
                details={
                    "script_id": str(script.id),
                    "voice_profile_code": profile.code,
                    "voice_profile_version": profile.version,
                    "lines": len(lines),
                    "provider": snapshot.provider,
                    "model": snapshot.model,
                },
            )
            return _Prepared(
                voiceover_id=record.id,
                lines=lines,
                profile=profile,
                snapshot=snapshot,
                idempotency=replay,
            )

    async def _produce(self, *, business_id: UUID, prepared: _Prepared, workdir: Path) -> _Produced:
        """Synthesize, measure and store every line. No database session is touched in here.

        The whole run carries a wall-clock ceiling on top of the per-call one, because this
        endpoint answers synchronously: eight slow lines under a 60-second per-call timeout would
        otherwise hold a request open for eight minutes.
        """

        segments: list[VoiceoverSegment] = []
        calls: list[_CallRecord] = []
        try:
            async with asyncio.timeout(self._settings.tts_total_timeout_seconds):
                for line in prepared.lines:
                    problem = await self._produce_line(
                        business_id=business_id,
                        prepared=prepared,
                        line=line,
                        workdir=workdir,
                        segments=segments,
                        calls=calls,
                    )
                    if problem is not None:
                        return _Produced(
                            segments=tuple(segments), calls=tuple(calls), problem=problem
                        )
        except TimeoutError:
            return _Produced(
                segments=tuple(segments),
                calls=tuple(calls),
                problem=self._provider_unavailable(),
            )
        return _Produced(segments=tuple(segments), calls=tuple(calls), problem=None)

    async def _produce_line(
        self,
        *,
        business_id: UUID,
        prepared: _Prepared,
        line: VoiceoverLine,
        workdir: Path,
        segments: list[VoiceoverSegment],
        calls: list[_CallRecord],
    ) -> ProblemException | None:
        """One line: call, measure, store. Returns the problem that ended the run, or `None`."""

        destination = workdir / f"segment-{line.index:03d}{VOICEOVER_AUDIO_FORMAT.extension}"
        started = time.monotonic()
        try:
            async with asyncio.timeout(self._settings.tts_timeout_seconds):
                audio = await self._tts.synthesize(
                    request=SynthesisRequest(
                        text=line.text,
                        voice_profile=prepared.profile,
                        output_format=VOICEOVER_AUDIO_FORMAT,
                        destination=destination,
                        max_output_bytes=self._settings.tts_max_audio_bytes,
                    ),
                    timeout_seconds=self._settings.tts_timeout_seconds,
                )
        except (TTSTransientError, TimeoutError):
            calls.append(self._call(prepared, None, started, _OUTCOME_FAILED))
            return self._provider_unavailable()
        except TTSPermanentError:
            calls.append(self._call(prepared, None, started, _OUTCOME_FAILED))
            return ProblemException(
                status=502,
                code="TTS_GENERATION_FAILED",
                title="Voiceover generation failed",
                detail="The speech provider rejected the request.",
            )
        except TTSDisabledError:
            # Unreachable through the factory — `descriptor.enabled` is checked before anything
            # is written. Handled anyway so a future adapter that decides at call time (a revoked
            # key, a region it may not serve) settles its row instead of leaving it `pending`.
            calls.append(self._call(prepared, None, started, _OUTCOME_FAILED))
            return self._not_configured()

        if audio.provider != prepared.snapshot.provider or audio.model != prepared.snapshot.model:
            # The snapshot said where the call would go. An adapter answering from somewhere else
            # breaks cost attribution and data-region routing at once.
            calls.append(self._call(prepared, audio, started, _OUTCOME_REJECTED))
            return ProblemException(
                status=502,
                code="TTS_ROUTE_MISMATCH",
                title="Provider route mismatch",
                detail="The provider answer did not match the recorded route.",
            )

        spent = sum(call.actual_cost_minor for call in calls) + audio.actual_cost_minor
        if spent > prepared.snapshot.max_cost_minor:
            # Stop the run rather than finish it: the remaining lines would each add to a bill
            # that has already passed the ceiling somebody set.
            calls.append(self._call(prepared, audio, started, _OUTCOME_OVER_BUDGET))
            return self._cost_limit_problem()

        try:
            measured = await self._probe.measure(
                path=audio.path, timeout_seconds=self._settings.tts_probe_timeout_seconds
            )
        except AudioProbeTransientError:
            calls.append(self._call(prepared, audio, started, _OUTCOME_FAILED))
            return ProblemException(
                status=503,
                code="VOICEOVER_AUDIO_UNMEASURABLE",
                title="Voiceover could not be measured",
                detail="The audio could not be measured. Try again.",
            )
        except AudioProbePermanentError:
            calls.append(self._call(prepared, audio, started, _OUTCOME_REJECTED))
            return self._audio_invalid()

        elapsed_total = total_duration_ms(segments) + measured.duration_ms
        if (
            not MIN_SEGMENT_AUDIO_MS <= measured.duration_ms <= MAX_SEGMENT_AUDIO_MS
            or elapsed_total > MAX_TOTAL_AUDIO_MS
        ):
            calls.append(self._call(prepared, audio, started, _OUTCOME_REJECTED))
            return self._audio_invalid()

        object_key = segment_object_key(
            business_id,
            prepared.voiceover_id,
            line.index,
            suffix=VOICEOVER_AUDIO_FORMAT.extension,
        )
        try:
            stored = await self._storage.persist_file(
                object_key=object_key,
                source_path=audio.path,
                content_type=audio.content_type,
            )
        except StorageUnavailableError:
            calls.append(self._call(prepared, audio, started, _OUTCOME_FAILED))
            return ProblemException(
                status=503,
                code="VOICEOVER_STORAGE_UNAVAILABLE",
                title="Voiceover could not be stored",
                detail="The audio could not be stored. Try again.",
            )
        except StoragePermanentError:
            calls.append(self._call(prepared, audio, started, _OUTCOME_REJECTED))
            return self._storage_metadata_invalid()
        if (
            stored.byte_size != audio.byte_size
            or stored.content_type.lower() != audio.content_type
            or stored.sha256_checksum.lower() != audio.sha256_checksum
        ):
            # What storage observed must equal what the adapter said it wrote. Otherwise the row
            # would describe one file and the bucket would hold another.
            calls.append(self._call(prepared, audio, started, _OUTCOME_REJECTED))
            return self._storage_metadata_invalid()

        calls.append(self._call(prepared, audio, started, _OUTCOME_SUCCEEDED))
        segments.append(
            VoiceoverSegment(
                index=line.index,
                purpose=line.purpose,
                object_key=object_key,
                content_type=audio.content_type,
                byte_size=stored.byte_size,
                sha256_checksum=stored.sha256_checksum.lower(),
                # The probe's answer, always. `declared_duration_ms` is kept beside it so a
                # provider that misreports its own output is visible in the record rather than
                # silently corrected.
                duration_ms=measured.duration_ms,
                declared_duration_ms=audio.declared_duration_ms,
                target_duration_ms=line.target_duration_ms,
            )
        )
        return None

    async def _settle(
        self,
        prepared: _Prepared,
        *,
        business_id: UUID,
        user_id: UUID,
        produced: _Produced,
        correlation_id: str,
    ) -> VoiceoverAsset:
        """Record what the calls cost and what they produced, then decide the row's fate.

        Usage rows are written here rather than one per call as the calls happen, following slice
        2B: the `pending` row and its route snapshot are the crash evidence, and reopening a
        transaction between every line would multiply connection churn on a single server for a
        record that is only read after the fact.
        """

        async with self._session.begin():
            voiceover = await self._lock(business_id, prepared.voiceover_id)
            usage = self._record_usage(
                business_id=business_id,
                snapshot=prepared.snapshot,
                calls=produced.calls,
                correlation_id=correlation_id,
            )
            await self._session.flush()
            voiceover.segments = serialize_segments(produced.segments)
            voiceover.total_duration_ms = total_duration_ms(produced.segments)
            voiceover.drift_ms = total_drift_ms(produced.segments)
            voiceover.provider_usage_id = usage.id if usage is not None else None
            voiceover.completed_at = datetime.now(UTC)
            if produced.problem is not None:
                voiceover.status = VoiceoverStatus.FAILED
                voiceover.failure_code = produced.problem.code
                self._audit(
                    business_id=business_id,
                    user_id=user_id,
                    action="content.voiceover.failed",
                    resource_id=voiceover.id,
                    correlation_id=correlation_id,
                    details={
                        "failure_code": produced.problem.code,
                        # Partial audio is a fact worth auditing: the objects exist and are
                        # attributable, they just do not add up to a usable voiceover.
                        "stored_segments": len(produced.segments),
                    },
                )
                await self._complete_idempotent(
                    prepared.idempotency,
                    response_status=produced.problem.status,
                    body=_failure_body(voiceover.id, produced.problem),
                )
            else:
                voiceover.status = VoiceoverStatus.GENERATED
                voiceover.failure_code = None
                self._audit(
                    business_id=business_id,
                    user_id=user_id,
                    action="content.voiceover.generated",
                    resource_id=voiceover.id,
                    correlation_id=correlation_id,
                    details={
                        "segments": len(produced.segments),
                        "total_duration_ms": voiceover.total_duration_ms,
                        "drift_ms": voiceover.drift_ms,
                        "voice_profile_code": voiceover.voice_profile_code,
                        "voice_profile_version": voiceover.voice_profile_version,
                    },
                )
                await self._complete_idempotent(
                    prepared.idempotency,
                    response_status=201,
                    body={
                        "voiceover_id": str(voiceover.id),
                        "status": VoiceoverStatus.GENERATED.value,
                    },
                )
        if produced.problem is not None:
            raise produced.problem
        return voiceover

    # --- reads ---------------------------------------------------------------------------

    async def get_voiceover(
        self, *, user_id: UUID, business_id: UUID, voiceover_id: UUID
    ) -> VoiceoverAsset:
        await self._authorize(user_id, business_id, ContentAction.VOICEOVER_READ)
        return await self._load(business_id, voiceover_id)

    async def list_voiceovers(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        cursor: Cursor | None,
        limit: int | None,
        script_id: UUID | None,
        status: VoiceoverStatus | None,
    ) -> Page[VoiceoverAsset]:
        await self._authorize(user_id, business_id, ContentAction.VOICEOVER_READ)
        page_size = resolve_limit(limit)
        rows = await self._repository.list_voiceovers(
            business_id, cursor=cursor, limit=page_size, script_id=script_id, status=status
        )
        return build_page(rows, limit=page_size, key=lambda row: (row.created_at, row.id))

    # --- input verification ---------------------------------------------------------------

    def _resolve_profile(self, code: str | None) -> VoiceProfile:
        """Resolve the requested voice, or the configured default.

        The two failures are kept apart deliberately. A caller naming an unknown voice made a
        client error; a deployment whose configured default is not in the registry made a
        configuration error, and answering `422` there would blame the wrong party.
        """

        if code is not None:
            profile = resolve_voice_profile(code)
            if profile is None:
                raise ProblemException(
                    status=422,
                    code="VOICEOVER_VOICE_PROFILE_UNKNOWN",
                    title="Unknown voice profile",
                    detail="The requested voice profile is not available.",
                )
            return profile
        default = resolve_voice_profile(self._settings.tts_default_voice_profile)
        if default is None:
            raise ProblemException(
                status=409,
                code="VOICEOVER_VOICE_PROFILE_NOT_CONFIGURED",
                title="No default voice profile",
                detail="This environment has no usable default voice profile.",
            )
        return default

    async def _require_voiceable_script(self, business_id: UUID, script_id: UUID) -> ContentScript:
        """The script must exist in *this* tenant and must have settled successfully.

        The query is tenant-scoped, so another business's script produces no row and the answer
        is the same `404` a made-up id gets: the endpoint cannot be used to learn which script
        ids are real somewhere else. A `pending` or `failed` script is refused separately,
        because it exists here and its problem is its state, not its ownership.
        """

        script = await self._repository.get_script(business_id, script_id)
        if script is None:
            raise self._not_found("VOICEOVER_SCRIPT_NOT_FOUND", "Script not found")
        if script.status is not ScriptStatus.GENERATED or not script.document:
            raise ProblemException(
                status=409,
                code="VOICEOVER_SCRIPT_NOT_USABLE",
                title="Script cannot be voiced",
                detail="Only a successfully generated script can be turned into a voiceover.",
            )
        return script

    @staticmethod
    def _require_lines(script: ContentScript) -> tuple[VoiceoverLine, ...]:
        try:
            return script_lines(script.document)
        except VoiceoverSourceError as error:
            raise ProblemException(
                status=422,
                code="VOICEOVER_SCRIPT_NOT_VOICEABLE",
                title="Script cannot be voiced",
                detail="The stored script does not contain lines that can be synthesized.",
                # The pointer names the location; the text is never echoed, because a script is
                # produced from transcript text lifted out of uploaded media.
                meta={"issue": error.code, "pointer": error.pointer},
            ) from error

    # --- routing and usage ------------------------------------------------------------------

    def _route_snapshot(
        self, *, descriptor: ProviderDescriptor, ceiling: int, estimated_cost_minor: int
    ) -> RouteSnapshot:
        return RouteSnapshot(
            capability=TTS_CAPABILITY,
            provider=descriptor.provider,
            model=descriptor.model,
            route_revision=self._settings.tts_route_revision,
            quality_tier=self._settings.tts_quality_tier,
            timeout_seconds=self._settings.tts_timeout_seconds,
            max_cost_minor=ceiling,
            estimated_cost_minor=estimated_cost_minor,
            currency=descriptor.currency,
            # Empty by construction. A voiceover that failed validation failed on our rules, and
            # a second provider is not a second opinion about those.
            fallbacks=(),
            data_region=self._settings.tts_data_region,
        )

    def _call(
        self,
        prepared: _Prepared,
        audio: AudioResult | None,
        started: float,
        outcome: str,
    ) -> _CallRecord:
        descriptor = self._tts.descriptor
        return _CallRecord(
            estimated_cost_minor=descriptor.estimated_cost_minor,
            actual_cost_minor=audio.actual_cost_minor if audio is not None else 0,
            currency=audio.currency if audio is not None else prepared.snapshot.currency,
            duration_ms=_elapsed_ms(started),
            outcome=outcome,
        )

    def _record_usage(
        self,
        *,
        business_id: UUID,
        snapshot: RouteSnapshot,
        calls: tuple[_CallRecord, ...],
        correlation_id: str,
    ) -> ProviderUsage | None:
        """One row per call (§39.1), all sharing this request's correlation id.

        Returns the last one, which is what the voiceover row points at: its `outcome` is the
        outcome of the run. `None` when no call happened at all, which today only occurs on a
        script with no voiceable lines — and that is refused before the row is written.
        """

        usage: ProviderUsage | None = None
        for call in calls:
            usage = ProviderUsage.from_measurement(
                business_id=business_id,
                capability=snapshot.capability,
                provider=snapshot.provider,
                model=snapshot.model,
                estimated_cost_minor=call.estimated_cost_minor,
                actual_cost_minor=call.actual_cost_minor,
                currency=call.currency,
                duration_ms=call.duration_ms,
                outcome=call.outcome,
                correlation_id=correlation_id,
            )
            usage.id = uuid4()
            self._session.add(usage)
        return usage

    # --- shared service plumbing ------------------------------------------------------------

    async def _authorize(self, user_id: UUID, business_id: UUID, action: ContentAction) -> None:
        """Membership first, then permission: an outsider gets `404`, a member gets `403`."""

        membership = await self._businesses.get_active_membership(business_id, user_id)
        if membership is None:
            raise self._not_found("BUSINESS_NOT_FOUND", "Business not found")
        if not permits_action(membership.role, action):
            raise ProblemException(
                status=403,
                code="INSUFFICIENT_PERMISSION",
                title="Forbidden",
                detail="You do not have this permission.",
            )

    async def _require_active_business(self, business_id: UUID) -> None:
        business = await self._businesses.get_business(business_id)
        if business is None:
            raise self._not_found("BUSINESS_NOT_FOUND", "Business not found")
        if business.status != BusinessStatus.ACTIVE:
            raise ProblemException(
                status=409,
                code="BUSINESS_NOT_MUTABLE",
                title="Business is not mutable",
                detail="Suspended or archived businesses cannot be changed.",
            )

    async def _begin_idempotent(
        self,
        *,
        business_id: UUID,
        user_id: UUID,
        key: str | None,
        payload: dict[str, object],
        correlation_id: str,
    ) -> _IdempotentRequest | None:
        if key is None:
            return None
        result = await IdempotencyService(OperationsRepository(self._session)).acquire(
            business_id=business_id,
            actor_user_id=user_id,
            operation="content.voiceover.generate",
            key=key,
            fingerprint=request_fingerprint(payload),
            correlation_id=correlation_id,
        )
        body = result.record.response_body or {}
        voiceover_id = body.get("voiceover_id") if result.is_replay else None
        if result.is_replay:
            problem = body.get("problem")
            if isinstance(problem, dict):
                # The same key returned the same answer the first time; a failed run replays as
                # the same failure rather than as a second set of paid calls.
                raise _rebuild_problem(problem)
        return _IdempotentRequest(
            record=result.record,
            voiceover_id=UUID(voiceover_id) if isinstance(voiceover_id, str) else None,
        )

    async def _complete_idempotent(
        self, request: _IdempotentRequest | None, *, response_status: int, body: dict[str, object]
    ) -> None:
        if request is None:
            return
        await OperationsService(self._session, self._settings).complete_idempotency(
            request.record, response_status=response_status, response_body=body
        )

    async def _load(self, business_id: UUID, voiceover_id: UUID) -> VoiceoverAsset:
        voiceover = await self._repository.get_voiceover(business_id, voiceover_id)
        if voiceover is None:
            raise self._not_found("VOICEOVER_NOT_FOUND", "Voiceover not found")
        return voiceover

    async def _lock(self, business_id: UUID, voiceover_id: UUID) -> VoiceoverAsset:
        voiceover = await self._repository.get_voiceover(business_id, voiceover_id, lock=True)
        if voiceover is None:
            raise self._not_found("VOICEOVER_NOT_FOUND", "Voiceover not found")
        return voiceover

    def _audit(
        self,
        *,
        business_id: UUID,
        user_id: UUID,
        action: str,
        resource_id: UUID,
        correlation_id: str,
        details: dict[str, object],
    ) -> None:
        OperationsRepository(self._session).add(
            AuditLog(
                id=uuid4(),
                business_id=business_id,
                actor_user_id=user_id,
                action=action,
                resource_type="voiceover_asset",
                resource_id=resource_id,
                correlation_id=correlation_id,
                details=details,
            )
        )

    @staticmethod
    def _not_configured() -> ProblemException:
        return ProblemException(
            status=503,
            code="TTS_NOT_CONFIGURED",
            title="Voiceover is not available",
            detail="No text-to-speech provider is configured for this environment.",
        )

    @staticmethod
    def _provider_unavailable() -> ProblemException:
        return ProblemException(
            status=503,
            code="TTS_PROVIDER_UNAVAILABLE",
            title="Voiceover provider unavailable",
            detail="The speech provider did not answer in time. Try again.",
        )

    @staticmethod
    def _cost_limit_problem() -> ProblemException:
        return ProblemException(
            status=409,
            code="TTS_COST_LIMIT_EXCEEDED",
            title="Cost ceiling exceeded",
            detail="This voiceover would exceed the configured provider cost ceiling.",
        )

    @staticmethod
    def _storage_metadata_invalid() -> ProblemException:
        return ProblemException(
            status=502,
            code="VOICEOVER_STORAGE_METADATA_INVALID",
            title="Stored voiceover does not match",
            detail="What storage holds does not match what was produced.",
        )

    @staticmethod
    def _audio_invalid() -> ProblemException:
        return ProblemException(
            status=502,
            code="VOICEOVER_AUDIO_INVALID",
            title="Voiceover audio is not usable",
            detail="The produced audio is not measurable speech of the expected length.",
        )

    @staticmethod
    def _not_found(code: str, title: str) -> ProblemException:
        return ProblemException(
            status=404, code=code, title=title, detail="The resource is not available."
        )


@dataclass(frozen=True, slots=True)
class _IdempotentRequest:
    record: IdempotencyKey
    voiceover_id: UUID | None


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _failure_body(voiceover_id: UUID, problem: ProblemException) -> dict[str, object]:
    return {
        "voiceover_id": str(voiceover_id),
        "status": VoiceoverStatus.FAILED.value,
        "problem": {
            "status": problem.status,
            "code": problem.code,
            "title": problem.title,
            "detail": problem.detail,
            "meta": problem.meta,
        },
    }


def _rebuild_problem(stored: dict[str, Any]) -> ProblemException:
    return ProblemException(
        status=int(stored.get("status", 502)),
        code=str(stored.get("code", "TTS_GENERATION_FAILED")),
        title=str(stored.get("title", "Voiceover generation failed")),
        detail=str(stored.get("detail", "The voiceover could not be produced.")),
        meta=stored.get("meta") if isinstance(stored.get("meta"), dict) else {},
    )
