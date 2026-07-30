"""Pure brand rules: normalization, money, campaign activity, and the health score.

Nothing here touches a database, a request, or a provider. These are the rules content
generation will later depend on, so they are deterministic and unit-testable in isolation:
"is this campaign usable right now" and "what is this brand missing" must answer identically
for the same inputs, forever, with no model in the loop.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.core.errors import ProblemException
from app.core.money import MAX_MINOR_UNITS, is_minor_units
from app.modules.brands.models import CampaignApprovalStatus, CampaignOfferStatus

MAX_TEXT_ENTRY_LENGTH = 300
MAX_LIST_ENTRIES = 100
MAX_COLOR_ENTRIES = 8
MAX_AUDIENCE_ENTRIES = 20
MAX_ASSET_ENTRIES = 20
MAX_LOCATION_ENTRIES = 50
MAX_PRICE_MINOR = MAX_MINOR_UNITS
"""The monetary bound is defined once, in `core/money.py`; this is the catalogue's name for it."""
MAX_DISCOUNT_PERCENT = 90
MAX_STOCK_LIMIT = 10**7
MIN_AGE = 13
MAX_AGE = 120

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_COLOR = re.compile(r"^#[0-9A-F]{6}$")
_LANGUAGE = re.compile(r"^[a-z]{2}(-[A-Za-z0-9]{2,8})?$")
_WHITESPACE = re.compile(r"\s+")
_ALLOWED_URL_SCHEMES = ("https://", "http://")


def invalid(detail: str) -> ProblemException:
    """A single validation rejection shape; the rejected value is never echoed back."""

    return ProblemException(
        status=400, code="REQUEST_INVALID", title="Invalid request", detail=detail
    )


def normalize_text(value: str, *, field: str, limit: int = MAX_TEXT_ENTRY_LENGTH) -> str:
    """Collapse whitespace and enforce a bound; empty-after-strip is a rejection, not a blank."""

    collapsed = _WHITESPACE.sub(" ", value).strip()
    if not collapsed:
        raise invalid(f"{field} must not be empty.")
    if len(collapsed) > limit:
        raise invalid(f"{field} must be at most {limit} characters.")
    return collapsed


def lookup_key(value: str) -> str:
    """Case- and accent-insensitive key so "Taze Hazırlanır" cannot be stored twice."""

    folded = unicodedata.normalize("NFKD", value).casefold()
    return _WHITESPACE.sub(" ", folded).strip()


def normalize_currency(value: str) -> str:
    """ISO-4217 alphabetic code, upper case. The code is stored beside every amount."""

    candidate = value.strip().upper()
    if not _CURRENCY.fullmatch(candidate):
        raise invalid("Currency must be a three-letter ISO-4217 code.")
    return candidate


def normalize_language(value: str) -> str:
    candidate = value.strip()
    if not _LANGUAGE.fullmatch(candidate):
        raise invalid("Communication language must be a BCP-47 code such as 'tr' or 'tr-TR'.")
    return candidate


def normalize_color(value: str) -> str:
    candidate = value.strip().upper()
    if not _COLOR.fullmatch(candidate):
        raise invalid("Colors must be six-digit hexadecimal values such as '#1A2B3C'.")
    return candidate


def normalize_url(value: str) -> str:
    candidate = value.strip()
    if len(candidate) > 2048 or not candidate.startswith(_ALLOWED_URL_SCHEMES):
        raise invalid("Landing page URL must be an http(s) URL of at most 2048 characters.")
    return candidate


def normalize_entries(values: list[str], *, field: str, limit: int = MAX_LIST_ENTRIES) -> list[str]:
    """Normalize a text list, drop case-insensitive duplicates, and keep the caller's order."""

    if len(values) > limit:
        raise invalid(f"{field} accepts at most {limit} entries.")
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        entry = normalize_text(value, field=field)
        key = lookup_key(entry)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(entry)
    return normalized


def normalize_price_minor(value: int) -> int:
    """Money is a count of minor units: a non-integer or negative price is not a price."""

    if not is_minor_units(value):
        raise invalid("Price must be a non-negative integer amount in minor units.")
    return value


@dataclass(frozen=True, slots=True)
class Money:
    """An integer amount of minor units and the currency it is counted in, never separated."""

    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if not is_minor_units(self.amount_minor):
            raise invalid("Monetary amounts must be integers in minor units.")


class CampaignActivity(StrEnum):
    """Why a campaign is or is not usable. One deterministic answer per record and instant."""

    ACTIVE = "active"
    NOT_STARTED = "not_started"
    EXPIRED = "expired"
    AWAITING_APPROVAL = "awaiting_approval"
    NOT_ACTIVE_STATUS = "not_active_status"


def evaluate_campaign_activity(
    *,
    status: CampaignOfferStatus,
    approval_status: CampaignApprovalStatus,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
) -> CampaignActivity:
    """Answer "may content quote this campaign at `now`" with no ambiguity.

    The window is half-open, `[starts_at, ends_at)`: a campaign is active at its exact start
    instant and already expired at its exact end instant. PRD §2.2 forbids generating content
    for a campaign whose date has passed, so the boundary has to fall on the safe side.
    """

    if status is not CampaignOfferStatus.ACTIVE:
        return CampaignActivity.NOT_ACTIVE_STATUS
    if approval_status not in (
        CampaignApprovalStatus.NOT_REQUIRED,
        CampaignApprovalStatus.APPROVED,
    ):
        return CampaignActivity.AWAITING_APPROVAL
    if now < starts_at:
        return CampaignActivity.NOT_STARTED
    if now >= ends_at:
        return CampaignActivity.EXPIRED
    return CampaignActivity.ACTIVE


@dataclass(frozen=True, slots=True)
class MediaInventory:
    """Counts of tenant media that finished ingest, as the health score sees them."""

    photo_count: int
    video_count: int


class MediaAssetPort(Protocol):
    """The read-only view of the media module that brand rules need, and nothing more.

    Declared here so the dependency direction is explicit and one-way: brands states what it
    needs, and the reader that satisfies it stays outside the pure rules. Brands never writes
    media, never learns a storage key, and never opens a second upload path.
    """

    async def media_inventory(self, business_id: UUID) -> MediaInventory: ...

    async def usable_asset_ids(
        self, business_id: UUID, asset_ids: Sequence[UUID]
    ) -> frozenset[UUID]: ...


class HealthComponentStatus(StrEnum):
    SATISFIED = "satisfied"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class BrandHealthComponent:
    key: str
    status: HealthComponentStatus
    detail: str


@dataclass(frozen=True, slots=True)
class BrandHealthSnapshot:
    """Everything the score reads, gathered once by the repository."""

    has_profile: bool
    has_display_name: bool
    has_tone: bool
    has_language: bool
    color_count: int
    logo_count: int
    active_product_count: int
    audience_count: int
    forbidden_claim_count: int
    approved_cta_count: int
    photo_count: int
    video_count: int
    campaign_offer_count: int


@dataclass(frozen=True, slots=True)
class BrandHealth:
    """Advisory signal only. Nothing in this module may refuse a write because of the score."""

    score: int
    components: tuple[BrandHealthComponent, ...]
    advisory: bool = True

    @property
    def missing_keys(self) -> tuple[str, ...]:
        return tuple(
            component.key
            for component in self.components
            if component.status is HealthComponentStatus.MISSING
        )

    @property
    def unavailable_keys(self) -> tuple[str, ...]:
        return tuple(
            component.key
            for component in self.components
            if component.status is HealthComponentStatus.UNAVAILABLE
        )


# PRD §10.4 lists eleven components. Three of them measure modules that do not exist yet;
# they are reported as `unavailable` and excluded from the denominator rather than counted as
# failures, because a score that punishes a tenant for an unbuilt feature is a false signal.
_UNAVAILABLE_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("connected_social_account", "Social connections are not part of this release."),
    ("publishing_hours", "Publishing schedule is not part of this release."),
    ("ad_conversion_tracking", "Advertising conversion tracking is not part of this release."),
)


def evaluate_brand_health(snapshot: BrandHealthSnapshot) -> BrandHealth:
    """Compute the advisory completeness score deterministically, with no model involved."""

    measured: tuple[tuple[str, bool, str], ...] = (
        (
            "business_profile_complete",
            snapshot.has_profile
            and snapshot.has_display_name
            and snapshot.has_tone
            and snapshot.has_language,
            "Brand name, tone and communication language are set.",
        ),
        (
            "logo_and_colors",
            snapshot.logo_count >= 1 and snapshot.color_count >= 2,
            "A logo asset and at least two brand colors are set.",
        ),
        (
            "product_catalog",
            snapshot.active_product_count >= 1,
            "At least one active product or service exists.",
        ),
        ("target_audience", snapshot.audience_count >= 1, "At least one target audience exists."),
        (
            "tone_rules",
            snapshot.forbidden_claim_count >= 1 and snapshot.approved_cta_count >= 1,
            "At least one forbidden claim and one approved call to action exist.",
        ),
        ("photo_library", snapshot.photo_count >= 5, "At least five usable photos exist."),
        ("video_library", snapshot.video_count >= 3, "At least three usable videos exist."),
        (
            "campaign_data",
            snapshot.campaign_offer_count >= 1,
            "At least one campaign record exists.",
        ),
    )
    components = [
        BrandHealthComponent(
            key=key,
            status=(
                HealthComponentStatus.SATISFIED if satisfied else HealthComponentStatus.MISSING
            ),
            detail=detail,
        )
        for key, satisfied, detail in measured
    ]
    components.extend(
        BrandHealthComponent(key=key, status=HealthComponentStatus.UNAVAILABLE, detail=detail)
        for key, detail in _UNAVAILABLE_COMPONENTS
    )
    satisfied_count = sum(
        1 for component in components if component.status is HealthComponentStatus.SATISFIED
    )
    return BrandHealth(
        score=round(100 * satisfied_count / len(measured)), components=tuple(components)
    )
