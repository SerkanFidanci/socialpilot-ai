"""Script generation: authorization, routing, the paid call, and deterministic assembly.

The shape that matters here is that a generation is **two transactions with a provider call
between them**, not one transaction wrapped around it.

The first commits a `pending` row carrying the route snapshot — capability, provider, model,
route revision, timeout and cost ceiling (ADR-007) — *before* anything is called. The second
settles it: one `provider_usage` row for cost attribution, then strict parsing, then
deterministic resolution of every verified field. Holding one transaction open across the call
would look tidier and would be wrong twice over: it would pin a PostgreSQL connection and
snapshot for the duration of a network round trip, and a crash mid-call would roll back the only
record that a billable call ever happened.

The consequence is deliberate: a row stuck in `pending` means a call may have been billed and
never settled. That is a fact worth being able to see, and it is invisible in any design where
the evidence is written after the answer comes back.

Everything the model produced is treated as untrusted. It is parsed against a closed schema,
its literal text is scanned for figures it had no business writing, and its slots are resolved
by code from the tenant's own records. A rejection is never retried against another provider:
"the model invented a price" is a policy failure, and shopping for a provider that disagrees is
not error handling.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.core.pagination import Cursor, Page, build_page, resolve_limit
from app.modules.businesses.models import Business, BusinessStatus
from app.modules.businesses.repository import BusinessRepository
from app.modules.content.models import ContentScript, PromptTemplate
from app.modules.content.policy import ContentAction, permits_action
from app.modules.content.repository import ContentFactsReader, ContentRepository, ScriptFactsReader
from app.modules.content.script import (
    SCRIPT_CAPABILITY,
    BrandBrief,
    CampaignBrief,
    ProductBrief,
    ProviderDescriptor,
    RouteSnapshot,
    ScenarioCode,
    ScriptBrief,
    ScriptContext,
    ScriptGenerationDisabledError,
    ScriptGenerationPermanentError,
    ScriptGenerationPort,
    ScriptGenerationRequest,
    ScriptGenerationResult,
    ScriptGenerationTransientError,
    ScriptSchemaError,
    ScriptStatus,
    SlotKind,
    SlotOffer,
    build_input_data,
    parse_script_output,
    resolve_script,
    sanitize_untrusted,
    serialize_draft,
)
from app.modules.operations.models import (
    AuditLog,
    IdempotencyKey,
    ProviderUsage,
)
from app.modules.operations.repository import OperationsRepository
from app.modules.operations.service import (
    IdempotencyService,
    OperationsService,
    request_fingerprint,
)

# How many segments the brief asks for. Not a schema bound (the parser allows 2–8): it is what a
# 10–30 second product reel actually needs (§14.1), and asking for a number keeps two
# generations from the same brief structurally comparable.
REQUESTED_SEGMENT_COUNT = 3

_OUTCOME_SUCCEEDED = "succeeded"
_OUTCOME_REJECTED = "rejected"
_OUTCOME_FAILED = "failed"
_OUTCOME_OVER_BUDGET = "over_budget"


@dataclass(frozen=True, slots=True)
class ScriptRequest:
    """What the caller asks for. Every reference is validated against the tenant before use."""

    scenario_code: ScenarioCode
    product_id: UUID
    cta_id: UUID
    campaign_offer_id: UUID | None
    source_asset_ids: tuple[UUID, ...]
    target_duration_ms: int | None

    def as_payload(self) -> dict[str, object]:
        """The canonical form the idempotency fingerprint is taken over."""

        return {
            "scenario_code": self.scenario_code.value,
            "product_id": str(self.product_id),
            "cta_id": str(self.cta_id),
            "campaign_offer_id": str(self.campaign_offer_id) if self.campaign_offer_id else None,
            "source_asset_ids": sorted(str(value) for value in self.source_asset_ids),
            "target_duration_ms": self.target_duration_ms,
        }


@dataclass(frozen=True, slots=True)
class _Prepared:
    """The committed intent to call a provider, plus everything the call needs."""

    script_id: UUID
    request: ScriptGenerationRequest
    snapshot: RouteSnapshot
    idempotency: IdempotencyKey | None
    timezone_name: str


@dataclass(frozen=True, slots=True)
class _Replay:
    script: ContentScript


class ScriptGenerationService:
    """PRD §18.1's contract, produced under §17.4 routing and §17.5 output safety."""

    def __init__(
        self, session: AsyncSession, settings: Settings, generator: ScriptGenerationPort
    ) -> None:
        self._session = session
        self._settings = settings
        self._generator = generator
        self._repository = ContentRepository(session)
        self._facts = ScriptFactsReader(session)
        self._content_facts = ContentFactsReader(session)
        self._businesses = BusinessRepository(session)

    # --- generation ---------------------------------------------------------------------

    async def generate(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        request: ScriptRequest,
        idempotency_key: str | None,
        correlation_id: str,
    ) -> ContentScript:
        prepared = await self._prepare(
            user_id=user_id,
            business_id=business_id,
            request=request,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        if isinstance(prepared, _Replay):
            return prepared.script

        started = time.monotonic()
        try:
            async with asyncio.timeout(self._settings.script_generation_timeout_seconds):
                result = await self._generator.generate(
                    request=prepared.request,
                    timeout_seconds=self._settings.script_generation_timeout_seconds,
                )
        except (ScriptGenerationTransientError, TimeoutError) as error:
            raise await self._settle_provider_failure(
                prepared,
                business_id=business_id,
                elapsed_ms=_elapsed_ms(started),
                status=503,
                code="SCRIPT_PROVIDER_UNAVAILABLE",
                title="Script provider unavailable",
                detail="The script provider did not answer in time. Try again.",
            ) from error
        except ScriptGenerationPermanentError as error:
            raise await self._settle_provider_failure(
                prepared,
                business_id=business_id,
                elapsed_ms=_elapsed_ms(started),
                status=502,
                code="SCRIPT_GENERATION_FAILED",
                title="Script generation failed",
                detail="The script provider rejected the request.",
            ) from error
        except ScriptGenerationDisabledError as error:
            # Unreachable through the factory — `descriptor.enabled` is checked before anything
            # is written. Handled anyway so a future adapter that decides at call time (a
            # revoked key, a region it may not serve) settles its row instead of leaving it
            # `pending` forever.
            raise await self._settle_provider_failure(
                prepared,
                business_id=business_id,
                elapsed_ms=_elapsed_ms(started),
                status=503,
                code="SCRIPT_GENERATION_NOT_CONFIGURED",
                title="Script generation is not available",
                detail="No script-generation provider is configured for this environment.",
            ) from error

        return await self._settle(
            prepared,
            business_id=business_id,
            user_id=user_id,
            result=result,
            elapsed_ms=_elapsed_ms(started),
            correlation_id=correlation_id,
        )

    async def _prepare(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        request: ScriptRequest,
        idempotency_key: str | None,
        correlation_id: str,
    ) -> _Prepared | _Replay:
        """Authorize, verify every declared input, and commit the intent to call.

        The cost ceiling and the disabled adapter are checked *before* the row is written, so a
        refused generation leaves no trace of an attempt that never happened.
        """

        descriptor = self._generator.descriptor
        if not descriptor.enabled:
            raise ProblemException(
                status=503,
                code="SCRIPT_GENERATION_NOT_CONFIGURED",
                title="Script generation is not available",
                detail="No script-generation provider is configured for this environment.",
            )
        ceiling = self._settings.script_generation_max_cost_minor
        if descriptor.estimated_cost_minor > ceiling:
            raise self._cost_limit_problem()

        async with self._session.begin():
            await self._authorize(user_id, business_id, ContentAction.SCRIPT_GENERATE)
            business = await self._require_active_business(business_id)
            replay = await self._begin_idempotent(
                business_id=business_id,
                user_id=user_id,
                key=idempotency_key,
                payload=request.as_payload(),
                correlation_id=correlation_id,
            )
            if replay is not None and replay.script_id is not None:
                return _Replay(script=await self._load(business_id, replay.script_id))

            brand = await self._facts.brand_brief(business_id)
            if brand is None:
                raise self._not_found("BRAND_PROFILE_NOT_FOUND", "Brand profile not found")
            product = await self._facts.product_brief(business_id, request.product_id)
            if product is None:
                raise self._input_not_found()
            if await self._facts.cta_text(business_id, request.cta_id) is None:
                raise self._input_not_found()
            campaign = await self._require_campaign(business_id, request.campaign_offer_id)
            await self._require_assets(business_id, request.source_asset_ids)

            template = await self._repository.active_prompt_template(request.scenario_code.value)
            if template is None:
                raise ProblemException(
                    status=409,
                    code="SCRIPT_PROMPT_TEMPLATE_MISSING",
                    title="No active prompt",
                    detail="This scenario has no active prompt version.",
                )

            brief = await self._build_brief(
                business_id=business_id,
                request=request,
                brand=brand,
                product=product,
                campaign=campaign,
            )
            snapshot = self._route_snapshot(descriptor=descriptor, ceiling=ceiling)
            record = ContentScript(
                id=uuid4(),
                business_id=business_id,
                scenario_code=request.scenario_code,
                status=ScriptStatus.PENDING,
                product_id=request.product_id,
                campaign_offer_id=request.campaign_offer_id,
                cta_id=request.cta_id,
                source_asset_ids=[str(value) for value in request.source_asset_ids],
                template=None,
                document=None,
                prompt_template_id=template.id,
                prompt_code=template.code,
                prompt_version=template.version,
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
                action="content.script.requested",
                resource_id=record.id,
                correlation_id=correlation_id,
                details={
                    "scenario_code": request.scenario_code.value,
                    "prompt_code": template.code,
                    "prompt_version": template.version,
                    "provider": snapshot.provider,
                    "model": snapshot.model,
                },
            )
            return _Prepared(
                script_id=record.id,
                request=self._provider_request(template, brief),
                snapshot=snapshot,
                idempotency=replay.record if replay is not None else None,
                timezone_name=business.timezone,
            )

    async def _settle(
        self,
        prepared: _Prepared,
        *,
        business_id: UUID,
        user_id: UUID,
        result: ScriptGenerationResult,
        elapsed_ms: int,
        correlation_id: str,
    ) -> ContentScript:
        """Record what the call cost, then decide whether its output may be kept."""

        async with self._session.begin():
            script = await self._lock(business_id, prepared.script_id)
            outcome, problem = await self._evaluate(
                prepared, business_id=business_id, result=result, script=script
            )
            usage = self._record_usage(
                business_id=business_id,
                snapshot=prepared.snapshot,
                result=result,
                elapsed_ms=elapsed_ms,
                outcome=outcome,
                correlation_id=correlation_id,
            )
            await self._session.flush()
            script.provider_usage_id = usage.id
            script.completed_at = datetime.now(UTC)
            if problem is not None:
                script.status = ScriptStatus.FAILED
                script.failure_code = _failure_code(problem)
                self._audit(
                    business_id=business_id,
                    user_id=user_id,
                    action="content.script.failed",
                    resource_id=script.id,
                    correlation_id=correlation_id,
                    details={"failure_code": script.failure_code, "outcome": outcome},
                )
                await self._complete_idempotent(
                    prepared.idempotency,
                    response_status=problem.status,
                    body=_failure_body(script.id, problem),
                )
            else:
                script.status = ScriptStatus.GENERATED
                self._audit(
                    business_id=business_id,
                    user_id=user_id,
                    action="content.script.generated",
                    resource_id=script.id,
                    correlation_id=correlation_id,
                    details={
                        "prompt_code": script.prompt_code,
                        "prompt_version": script.prompt_version,
                        "segments": len(_segments(script.document)),
                    },
                )
                await self._complete_idempotent(
                    prepared.idempotency,
                    response_status=201,
                    body={"script_id": str(script.id), "status": ScriptStatus.GENERATED.value},
                )
        if problem is not None:
            raise problem
        return script

    async def _evaluate(
        self,
        prepared: _Prepared,
        *,
        business_id: UUID,
        result: ScriptGenerationResult,
        script: ContentScript,
    ) -> tuple[str, ProblemException | None]:
        """Parse, resolve and (only then) attach the document. Returns the usage outcome."""

        if result.provider != prepared.snapshot.provider or result.model != prepared.snapshot.model:
            # The snapshot said where the call would go. An adapter answering from somewhere
            # else breaks cost attribution and privacy routing at once, so it is a rejection
            # rather than a curiosity.
            return _OUTCOME_REJECTED, ProblemException(
                status=502,
                code="SCRIPT_ROUTE_MISMATCH",
                title="Provider route mismatch",
                detail="The provider answer did not match the recorded route.",
            )
        if result.actual_cost_minor > prepared.snapshot.max_cost_minor:
            return _OUTCOME_OVER_BUDGET, self._cost_limit_problem()

        try:
            draft = parse_script_output(result.output_json)
        except ScriptSchemaError as error:
            return _OUTCOME_REJECTED, ProblemException(
                status=422,
                code="SCRIPT_PROVIDER_OUTPUT_INVALID",
                title="Script output is not valid",
                detail="The generated script does not match the script contract.",
                # The pointer names the location; the rejected text is never echoed, because a
                # generation is produced from transcript text lifted out of uploaded media.
                meta={"issue": error.code, "pointer": error.pointer},
            )

        # Values are read again here, at settlement, rather than reused from the request: a
        # price row can close and a campaign can expire while a provider is thinking, and the
        # script that gets stored must only contain values that were true when it was stored.
        now = datetime.now(UTC)
        context = ScriptContext(
            forbidden_terms=await self._content_facts.forbidden_terms(business_id),
            values=await self._facts.slot_values(
                business_id,
                product_id=script.product_id,
                campaign_offer_id=script.campaign_offer_id,
                cta_id=script.cta_id,
                timezone_name=prepared.timezone_name,
                now=now,
            ),
            # Only the CTA the request declared. Another approved CTA of the same tenant is
            # still a CTA the caller did not ask for, and a script that swapped one in would
            # make the request's own record wrong.
            approved_cta_ids=frozenset[UUID](() if script.cta_id is None else (script.cta_id,)),
        )
        outcome = resolve_script(draft, context=context)
        if not outcome.ok:
            return _OUTCOME_REJECTED, ProblemException(
                status=422,
                code="SCRIPT_VALIDATION_FAILED",
                title="Script cannot be used",
                detail="The generated script failed content validation.",
                meta={
                    "issues": [
                        {"code": issue.code, "pointer": issue.pointer} for issue in outcome.issues
                    ]
                },
            )
        script.template = serialize_draft(draft)
        script.document = outcome.document
        return _OUTCOME_SUCCEEDED, None

    async def _settle_provider_failure(
        self,
        prepared: _Prepared,
        *,
        business_id: UUID,
        elapsed_ms: int,
        status: int,
        code: str,
        title: str,
        detail: str,
    ) -> ProblemException:
        """Close out a call that never returned an answer, and still record what it cost.

        A provider that timed out may well have billed for the work. Writing the usage row on
        this path is the difference between a cost report that reconciles and one that quietly
        under-counts every failure.
        """

        problem = ProblemException(status=status, code=code, title=title, detail=detail)
        async with self._session.begin():
            script = await self._lock(business_id, prepared.script_id)
            usage = self._record_usage(
                business_id=business_id,
                snapshot=prepared.snapshot,
                result=None,
                elapsed_ms=elapsed_ms,
                outcome=_OUTCOME_FAILED,
                correlation_id=script.correlation_id,
            )
            await self._session.flush()
            script.provider_usage_id = usage.id
            script.status = ScriptStatus.FAILED
            script.failure_code = code
            script.completed_at = datetime.now(UTC)
            await self._complete_idempotent(
                prepared.idempotency, response_status=status, body=_failure_body(script.id, problem)
            )
        return problem

    # --- reads ---------------------------------------------------------------------------

    async def get_script(
        self, *, user_id: UUID, business_id: UUID, script_id: UUID
    ) -> ContentScript:
        await self._authorize(user_id, business_id, ContentAction.SCRIPT_READ)
        return await self._load(business_id, script_id)

    async def list_scripts(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        cursor: Cursor | None,
        limit: int | None,
        scenario_code: ScenarioCode | None,
        status: ScriptStatus | None,
    ) -> Page[ContentScript]:
        await self._authorize(user_id, business_id, ContentAction.SCRIPT_READ)
        page_size = resolve_limit(limit)
        rows = await self._repository.list_scripts(
            business_id,
            cursor=cursor,
            limit=page_size,
            scenario_code=scenario_code,
            status=status,
        )
        return build_page(rows, limit=page_size, key=lambda row: (row.created_at, row.id))

    # --- assembly ------------------------------------------------------------------------

    async def _build_brief(
        self,
        *,
        business_id: UUID,
        request: ScriptRequest,
        brand: BrandBrief,
        product: ProductBrief,
        campaign: CampaignBrief | None,
    ) -> ScriptBrief:
        """Assemble what the model may know — which never includes a price or a date."""

        limit = self._settings.script_generation_max_brief_chars
        slots = [SlotOffer(kind=SlotKind.CTA, reference_id=request.cta_id, label="Onaylı CTA")]
        if await self._facts.current_price(business_id, request.product_id) is not None:
            # The slot is offered because an open price row exists; the *value* stays here.
            slots.insert(
                0,
                SlotOffer(
                    kind=SlotKind.PRICE, reference_id=request.product_id, label="Güncel fiyat"
                ),
            )
        if campaign is not None:
            slots.extend(
                (
                    SlotOffer(
                        kind=SlotKind.CAMPAIGN_TITLE,
                        reference_id=campaign.campaign_id,
                        label="Kampanya adı",
                    ),
                    SlotOffer(
                        kind=SlotKind.CAMPAIGN_END,
                        reference_id=campaign.campaign_id,
                        label="Kampanya son günü",
                    ),
                )
            )
        notes = await self._facts.media_notes(
            business_id,
            request.source_asset_ids,
            max_notes=self._settings.script_generation_max_notes,
            max_chars=self._settings.script_generation_max_note_chars,
        )
        return ScriptBrief(
            scenario_code=request.scenario_code,
            language=sanitize_untrusted(brand.language, max_chars=16),
            brand_name=sanitize_untrusted(brand.name, max_chars=limit),
            brand_tone=sanitize_untrusted(brand.tone, max_chars=limit),
            product_name=sanitize_untrusted(product.name, max_chars=limit),
            product_category=_optional(product.category, limit),
            product_description=_optional(product.description, limit),
            campaign_name=_optional(campaign.name if campaign else None, limit),
            target_duration_ms=(
                request.target_duration_ms or self._settings.script_generation_target_duration_ms
            ),
            segment_count=REQUESTED_SEGMENT_COUNT,
            slots=tuple(slots),
            notes=notes,
        )

    def _provider_request(
        self, template: PromptTemplate, brief: ScriptBrief
    ) -> ScriptGenerationRequest:
        return ScriptGenerationRequest(
            system_prompt=template.system_prompt,
            instruction=template.user_template,
            # Data and instructions stay in separate fields: everything tenant- or media-derived
            # travels here, and nothing is concatenated into the two strings above (§17.5).
            input_data=build_input_data(brief),
            output_schema=dict(template.output_schema),
            max_output_bytes=self._settings.script_generation_max_output_bytes,
        )

    def _route_snapshot(self, *, descriptor: ProviderDescriptor, ceiling: int) -> RouteSnapshot:
        return RouteSnapshot(
            capability=SCRIPT_CAPABILITY,
            provider=descriptor.provider,
            model=descriptor.model,
            route_revision=self._settings.script_generation_route_revision,
            quality_tier=self._settings.script_generation_quality_tier,
            timeout_seconds=self._settings.script_generation_timeout_seconds,
            max_cost_minor=ceiling,
            estimated_cost_minor=descriptor.estimated_cost_minor,
            currency=descriptor.currency,
            # Empty by construction. A script rejected for inventing a price is a policy
            # failure, not a transient one, and a second provider is not a second opinion.
            fallbacks=(),
            data_region=self._settings.script_generation_data_region,
        )

    def _record_usage(
        self,
        *,
        business_id: UUID,
        snapshot: RouteSnapshot,
        result: ScriptGenerationResult | None,
        elapsed_ms: int,
        outcome: str,
        correlation_id: str,
    ) -> ProviderUsage:
        usage = ProviderUsage.from_measurement(
            business_id=business_id,
            capability=snapshot.capability,
            provider=snapshot.provider,
            model=snapshot.model,
            estimated_cost_minor=snapshot.estimated_cost_minor,
            actual_cost_minor=result.actual_cost_minor if result is not None else 0,
            currency=result.currency if result is not None else snapshot.currency,
            duration_ms=elapsed_ms,
            outcome=outcome,
            correlation_id=correlation_id,
        )
        usage.id = uuid4()
        self._session.add(usage)
        return usage

    # --- input verification ---------------------------------------------------------------

    async def _require_campaign(
        self, business_id: UUID, campaign_offer_id: UUID | None
    ) -> CampaignBrief | None:
        if campaign_offer_id is None:
            return None
        campaign = await self._facts.campaign_brief(
            business_id, campaign_offer_id, now=datetime.now(UTC)
        )
        if campaign is None:
            raise self._input_not_found()
        if not campaign.active:
            # Refused before the call rather than after: generating copy around a campaign that
            # is already over spends money to produce something that cannot be published.
            # Settlement checks the window again, because it can close in between.
            raise ProblemException(
                status=409,
                code="SCRIPT_CAMPAIGN_NOT_ACTIVE",
                title="Campaign is not active",
                detail="The campaign is not active, so it cannot appear in a script.",
            )
        return campaign

    async def _require_assets(self, business_id: UUID, asset_ids: tuple[UUID, ...]) -> None:
        if not asset_ids:
            return
        if len(asset_ids) > self._settings.script_generation_max_source_assets:
            raise ProblemException(
                status=422,
                code="SCRIPT_TOO_MANY_SOURCE_ASSETS",
                title="Too many source assets",
                detail="Fewer source assets are needed for one script.",
            )
        known = await self._facts.known_asset_ids(business_id, asset_ids)
        if set(asset_ids) - known:
            raise self._input_not_found()

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

    async def _require_active_business(self, business_id: UUID) -> Business:
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
        return business

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
            operation="content.script.generate",
            key=key,
            fingerprint=request_fingerprint(payload),
            correlation_id=correlation_id,
        )
        body = result.record.response_body or {}
        script_id = body.get("script_id") if result.is_replay else None
        if result.is_replay:
            problem = body.get("problem")
            if isinstance(problem, dict):
                # The same key returned the same answer the first time; a failed generation
                # replays as the same failure rather than as a second paid attempt.
                raise _rebuild_problem(problem)
        return _IdempotentRequest(
            record=result.record,
            script_id=UUID(script_id) if isinstance(script_id, str) else None,
        )

    async def _complete_idempotent(
        self, record: IdempotencyKey | None, *, response_status: int, body: dict[str, object]
    ) -> None:
        if record is None:
            return
        await OperationsService(self._session, self._settings).complete_idempotency(
            record, response_status=response_status, response_body=body
        )

    async def _load(self, business_id: UUID, script_id: UUID) -> ContentScript:
        script = await self._repository.get_script(business_id, script_id)
        if script is None:
            raise self._not_found("SCRIPT_NOT_FOUND", "Script not found")
        return script

    async def _lock(self, business_id: UUID, script_id: UUID) -> ContentScript:
        script = await self._repository.get_script(business_id, script_id, lock=True)
        if script is None:
            raise self._not_found("SCRIPT_NOT_FOUND", "Script not found")
        return script

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
                resource_type="content_script",
                resource_id=resource_id,
                correlation_id=correlation_id,
                details=details,
            )
        )

    @staticmethod
    def _cost_limit_problem() -> ProblemException:
        return ProblemException(
            status=409,
            code="SCRIPT_COST_LIMIT_EXCEEDED",
            title="Cost ceiling exceeded",
            detail="This generation would exceed the configured provider cost ceiling.",
        )

    @staticmethod
    def _input_not_found() -> ProblemException:
        """One rejection for every unusable input reference.

        A product that does not exist and a product belonging to another tenant produce the same
        `404` with no identifier echoed, so the endpoint cannot be used to discover which ids are
        real somewhere else in the system.
        """

        return ProblemException(
            status=404,
            code="SCRIPT_INPUT_NOT_FOUND",
            title="Script input not found",
            detail="A referenced product, campaign, CTA or asset is not available.",
        )

    @staticmethod
    def _not_found(code: str, title: str) -> ProblemException:
        return ProblemException(
            status=404, code=code, title=title, detail="The resource is not available."
        )


@dataclass(frozen=True, slots=True)
class _IdempotentRequest:
    record: IdempotencyKey
    script_id: UUID | None


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _optional(value: str | None, limit: int) -> str | None:
    return sanitize_untrusted(value, max_chars=limit) if value else None


def _segments(document: dict[str, object] | None) -> list[object]:
    segments = (document or {}).get("segments")
    return segments if isinstance(segments, list) else []


def _failure_code(problem: ProblemException) -> str:
    """The most specific documented code available, for the row's `failure_code` column.

    A validation failure carries its issue codes in `meta`; the first one says more than the
    envelope does, and it is what an operator scanning failed generations wants to see.
    """

    issues = problem.meta.get("issues")
    if isinstance(issues, list) and issues:
        first = issues[0]
        if isinstance(first, dict) and isinstance(first.get("code"), str):
            return str(first["code"])
    issue = problem.meta.get("issue")
    if isinstance(issue, str):
        return issue
    return problem.code


def _failure_body(script_id: UUID, problem: ProblemException) -> dict[str, object]:
    return {
        "script_id": str(script_id),
        "status": ScriptStatus.FAILED.value,
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
        status=int(stored.get("status", 422)),
        code=str(stored.get("code", "SCRIPT_VALIDATION_FAILED")),
        title=str(stored.get("title", "Script cannot be used")),
        detail=str(stored.get("detail", "The generated script failed content validation.")),
        meta=stored.get("meta") if isinstance(stored.get("meta"), dict) else {},
    )
