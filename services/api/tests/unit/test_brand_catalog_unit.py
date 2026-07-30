"""Brand rules that must hold without a database: money, roles, campaign windows, score."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, Float, Integer, Numeric

from app.core.errors import ProblemException
from app.main import create_app
from app.modules.brands.domain import (
    MAX_LIST_ENTRIES,
    MAX_TEXT_ENTRY_LENGTH,
    BrandHealthSnapshot,
    CampaignActivity,
    HealthComponentStatus,
    Money,
    evaluate_brand_health,
    evaluate_campaign_activity,
    lookup_key,
    normalize_color,
    normalize_currency,
    normalize_entries,
    normalize_language,
    normalize_price_minor,
    normalize_text,
    normalize_url,
)
from app.modules.brands.models import (
    ApprovedClaim,
    ApprovedCta,
    BrandAsset,
    BrandProfile,
    CampaignApprovalStatus,
    CampaignOffer,
    CampaignOfferProduct,
    CampaignOfferStatus,
    ForbiddenClaim,
    Product,
    ProductPrice,
    TargetAudience,
)
from app.modules.brands.policy import BrandAction, permits_action, required_permission
from app.modules.businesses.models import BusinessRole
from app.modules.businesses.policy import Permission

BRAND_MODELS = (
    ApprovedClaim,
    ApprovedCta,
    BrandAsset,
    BrandProfile,
    CampaignOffer,
    CampaignOfferProduct,
    ForbiddenClaim,
    Product,
    ProductPrice,
    TargetAudience,
)
BRAND_PACKAGE = Path(__file__).resolve().parents[2] / "app" / "modules" / "brands"
START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 8, 31, 21, 0, tzinfo=UTC)


def offer_activity(**overrides: object) -> CampaignActivity:
    values: dict[str, object] = {
        "status": CampaignOfferStatus.ACTIVE,
        "approval_status": CampaignApprovalStatus.NOT_REQUIRED,
        "starts_at": START,
        "ends_at": END,
        "now": START + timedelta(days=1),
    }
    values.update(overrides)
    return evaluate_campaign_activity(**values)  # type: ignore[arg-type]


def snapshot(**overrides: object) -> BrandHealthSnapshot:
    values: dict[str, object] = {
        "has_profile": True,
        "has_display_name": True,
        "has_tone": True,
        "has_language": True,
        "color_count": 3,
        "logo_count": 1,
        "active_product_count": 2,
        "audience_count": 1,
        "forbidden_claim_count": 1,
        "approved_cta_count": 1,
        "photo_count": 5,
        "video_count": 3,
        "campaign_offer_count": 1,
    }
    values.update(overrides)
    return BrandHealthSnapshot(**values)  # type: ignore[arg-type]


# --- money -------------------------------------------------------------------------------


def test_monetary_columns_are_integer_minor_units_and_never_floating() -> None:
    """A price is a count of minor units. One float anywhere and rounding becomes revenue."""

    money_columns = [
        column
        for model in BRAND_MODELS
        for column in model.__table__.columns
        if column.name.endswith("_minor")
    ]
    assert {column.name for column in money_columns} == {"price_minor", "discount_amount_minor"}
    for column in money_columns:
        assert isinstance(column.type, BigInteger), column.name
    for model in BRAND_MODELS:
        for column in model.__table__.columns:
            assert not isinstance(column.type, Float | Numeric), f"{model.__name__}.{column.name}"
    assert isinstance(ProductPrice.__table__.columns["price_minor"].type, Integer | BigInteger)


def test_brand_module_source_contains_no_floating_money_conversion() -> None:
    for path in sorted(BRAND_PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for forbidden in ("float(", "Decimal", "Numeric", "Float"):
            assert forbidden not in source, f"{path.name} references {forbidden}"


def test_no_route_module_steals_another_module_s_schema_name() -> None:
    """Two route modules with the same response class name silently rename *both* schemas.

    FastAPI falls back to a fully qualified name (`app__api__routes__media__AssetResponse`) when
    two Pydantic models share a class name, so adding a router can rewrite an existing module's
    public schema name and break generated clients. A `__` in any schema name means that
    happened; the fix is to rename the newcomer, not to accept the qualified name.
    """

    schemas = create_app().openapi()["components"]["schemas"]
    assert [name for name in schemas if "__" in name] == []
    assert "AssetResponse" in schemas


def test_public_contract_exposes_money_as_an_integer() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    price = json.dumps(schemas["ProductResponse"]["properties"]["price_minor"])
    assert "integer" in price
    assert "number" not in price
    payload = json.dumps(schemas["PricePayload"]["properties"]["price_minor"])
    assert "integer" in payload and "number" not in payload


def test_money_refuses_a_non_integer_amount() -> None:
    assert Money(amount_minor=16500, currency="TRY").amount_minor == 16500
    with pytest.raises(ProblemException):
        Money(amount_minor=165.5, currency="TRY")  # type: ignore[arg-type]


def test_price_bounds_and_currency_normalization() -> None:
    assert normalize_price_minor(0) == 0
    with pytest.raises(ProblemException):
        normalize_price_minor(-1)
    with pytest.raises(ProblemException):
        normalize_price_minor(10**13)
    assert normalize_currency(" try ") == "TRY"
    for value in ("TRYY", "T1Y", "", "€"):
        with pytest.raises(ProblemException):
            normalize_currency(value)


# --- role matrix -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "action", "allowed"),
    [
        (BusinessRole.OWNER, BrandAction.BRAND_WRITE, True),
        (BusinessRole.OWNER, BrandAction.CATALOG_WRITE, True),
        (BusinessRole.OWNER, BrandAction.CAMPAIGN_WRITE, True),
        (BusinessRole.ADMIN, BrandAction.BRAND_WRITE, True),
        (BusinessRole.ADMIN, BrandAction.CATALOG_WRITE, True),
        (BusinessRole.ADMIN, BrandAction.CAMPAIGN_WRITE, True),
        (BusinessRole.EDITOR, BrandAction.BRAND_READ, True),
        (BusinessRole.EDITOR, BrandAction.BRAND_WRITE, False),
        (BusinessRole.EDITOR, BrandAction.CATALOG_WRITE, False),
        (BusinessRole.EDITOR, BrandAction.CAMPAIGN_WRITE, False),
        (BusinessRole.VIEWER, BrandAction.CATALOG_READ, True),
        (BusinessRole.VIEWER, BrandAction.BRAND_WRITE, False),
        (BusinessRole.VIEWER, BrandAction.CATALOG_WRITE, False),
        (BusinessRole.VIEWER, BrandAction.CAMPAIGN_WRITE, False),
    ],
)
def test_brand_role_matrix(role: BusinessRole, action: BrandAction, allowed: bool) -> None:
    assert permits_action(role, action) is allowed


def test_every_role_has_an_answer_for_every_brand_action() -> None:
    """A new role must be given a brand answer deliberately, not inherit one by accident."""

    for role in BusinessRole:
        for action in BrandAction:
            assert isinstance(permits_action(role, action), bool)


def test_brand_actions_reuse_the_central_permission_table() -> None:
    """Brand authorization is a mapping onto `businesses`, not a second policy table."""

    assert set(ACTION_TO_PERMISSION.values()) <= {
        Permission.BUSINESS_READ,
        Permission.BUSINESS_UPDATE,
    }
    assert required_permission(BrandAction.BRAND_WRITE) is Permission.BUSINESS_UPDATE
    assert required_permission(BrandAction.BRAND_READ) is Permission.BUSINESS_READ


ACTION_TO_PERMISSION = {action: required_permission(action) for action in BrandAction}


# --- campaign activity -------------------------------------------------------------------


def test_campaign_is_active_inside_its_window() -> None:
    assert offer_activity() is CampaignActivity.ACTIVE


def test_campaign_window_is_half_open_at_both_boundaries() -> None:
    """Start is inclusive, end is exclusive: the last second must not quote a dead campaign."""

    assert offer_activity(now=START) is CampaignActivity.ACTIVE
    assert offer_activity(now=END - timedelta(microseconds=1)) is CampaignActivity.ACTIVE
    assert offer_activity(now=END) is CampaignActivity.EXPIRED
    assert offer_activity(now=END + timedelta(microseconds=1)) is CampaignActivity.EXPIRED


def test_campaign_before_its_window_is_not_started() -> None:
    assert offer_activity(now=START - timedelta(seconds=1)) is CampaignActivity.NOT_STARTED


def test_expired_campaign_stays_inactive_regardless_of_approval() -> None:
    assert (
        offer_activity(
            now=END + timedelta(days=365), approval_status=CampaignApprovalStatus.APPROVED
        )
        is CampaignActivity.EXPIRED
    )


@pytest.mark.parametrize("status", [CampaignOfferStatus.DRAFT, CampaignOfferStatus.CANCELLED])
def test_non_active_status_is_never_usable(status: CampaignOfferStatus) -> None:
    assert offer_activity(status=status) is CampaignActivity.NOT_ACTIVE_STATUS


@pytest.mark.parametrize(
    "approval",
    [CampaignApprovalStatus.PENDING, CampaignApprovalStatus.REJECTED],
)
def test_unapproved_campaign_is_not_usable(approval: CampaignApprovalStatus) -> None:
    assert offer_activity(approval_status=approval) is CampaignActivity.AWAITING_APPROVAL


def test_activity_is_deterministic_for_identical_inputs() -> None:
    assert [offer_activity() for _ in range(5)] == [CampaignActivity.ACTIVE] * 5


# --- brand health ------------------------------------------------------------------------


def test_complete_brand_scores_one_hundred_and_lists_unbuilt_components() -> None:
    health = evaluate_brand_health(snapshot())
    assert health.score == 100
    assert health.advisory is True
    assert health.missing_keys == ()
    assert health.unavailable_keys == (
        "connected_social_account",
        "publishing_hours",
        "ad_conversion_tracking",
    )


def test_empty_brand_scores_zero_without_blocking_anything() -> None:
    health = evaluate_brand_health(
        snapshot(
            has_profile=False,
            has_display_name=False,
            has_tone=False,
            has_language=False,
            color_count=0,
            logo_count=0,
            active_product_count=0,
            audience_count=0,
            forbidden_claim_count=0,
            approved_cta_count=0,
            photo_count=0,
            video_count=0,
            campaign_offer_count=0,
        )
    )
    assert health.score == 0
    assert health.advisory is True
    assert len(health.missing_keys) == 8


def test_score_counts_only_measurable_components() -> None:
    """Eight measured components: a missing unbuilt module must not lower the score."""

    health = evaluate_brand_health(snapshot(photo_count=0, video_count=0))
    assert health.score == 75
    assert set(health.missing_keys) == {"photo_library", "video_library"}
    measured = [
        component
        for component in health.components
        if component.status is not HealthComponentStatus.UNAVAILABLE
    ]
    assert len(measured) == 8


def test_thresholds_are_exact() -> None:
    assert "photo_library" in evaluate_brand_health(snapshot(photo_count=4)).missing_keys
    assert "photo_library" not in evaluate_brand_health(snapshot(photo_count=5)).missing_keys
    assert "video_library" in evaluate_brand_health(snapshot(video_count=2)).missing_keys
    assert "video_library" not in evaluate_brand_health(snapshot(video_count=3)).missing_keys
    assert "logo_and_colors" in evaluate_brand_health(snapshot(color_count=1)).missing_keys
    assert "tone_rules" in evaluate_brand_health(snapshot(approved_cta_count=0)).missing_keys


def test_health_is_deterministic() -> None:
    assert len({evaluate_brand_health(snapshot()).score for _ in range(10)}) == 1


# --- normalization and limits ------------------------------------------------------------


def test_text_entries_are_bounded_and_never_blank() -> None:
    assert normalize_text("  Soğuk   Latte ", field="Product name") == "Soğuk Latte"
    for value in ("", "   ", "\n\t"):
        with pytest.raises(ProblemException) as error:
            normalize_text(value, field="Product name")
        assert error.value.status == 400
    with pytest.raises(ProblemException):
        normalize_text("x" * (MAX_TEXT_ENTRY_LENGTH + 1), field="Claim")


def test_entry_lists_drop_case_insensitive_duplicates_and_cap_length() -> None:
    assert normalize_entries(
        ["Taze hazırlanır", "taze  hazırlanır", "Hemen al"], field="Approved claims"
    ) == ["Taze hazırlanır", "Hemen al"]
    with pytest.raises(ProblemException):
        normalize_entries(["value"] * (MAX_LIST_ENTRIES + 1), field="Approved claims")
    assert lookup_key("Taze Hazırlanır") == lookup_key("taze hazırlanır")


def test_language_color_and_url_normalization() -> None:
    assert normalize_language("tr") == "tr"
    assert normalize_language("tr-TR") == "tr-TR"
    for value in ("turkish", "TR", "t", "tr_TR"):
        with pytest.raises(ProblemException):
            normalize_language(value)
    assert normalize_color("#1a2b3c") == "#1A2B3C"
    for value in ("1A2B3C", "#GG0000", "#123"):
        with pytest.raises(ProblemException):
            normalize_color(value)
    assert normalize_url("https://example.test/menu") == "https://example.test/menu"
    for value in ("javascript:alert(1)", "ftp://example.test", "example.test"):
        with pytest.raises(ProblemException):
            normalize_url(value)
