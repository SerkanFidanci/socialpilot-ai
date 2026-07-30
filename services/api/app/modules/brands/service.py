"""Brand, catalogue and campaign application services with tenant and role enforcement.

This is the layer content generation will trust. Its job is to make one promise: everything
readable through it is a record a human entered and a role was allowed to enter — no price,
date, coupon or claim in this module was ever produced by a model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.core.pagination import Cursor, Page, build_page, resolve_limit
from app.modules.brands.domain import (
    MAX_AGE,
    MAX_ASSET_ENTRIES,
    MAX_AUDIENCE_ENTRIES,
    MAX_COLOR_ENTRIES,
    MAX_DISCOUNT_PERCENT,
    MAX_LOCATION_ENTRIES,
    MAX_STOCK_LIMIT,
    MIN_AGE,
    BrandHealth,
    BrandHealthSnapshot,
    CampaignActivity,
    MediaAssetPort,
    Money,
    evaluate_brand_health,
    evaluate_campaign_activity,
    invalid,
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
    BrandAssetRole,
    BrandProfile,
    CampaignApprovalStatus,
    CampaignOffer,
    CampaignOfferProduct,
    CampaignOfferStatus,
    DiscountType,
    ForbiddenClaim,
    Product,
    ProductPrice,
    ProductStatus,
    StockStatus,
    TargetAudience,
)
from app.modules.brands.policy import BrandAction, permits_action
from app.modules.brands.repository import BrandRepository, MediaAssetReader
from app.modules.businesses.models import BusinessMember, BusinessStatus
from app.modules.businesses.repository import BusinessRepository
from app.modules.operations.models import AuditLog, IdempotencyKey
from app.modules.operations.repository import OperationsRepository
from app.modules.operations.service import (
    IdempotencyService,
    OperationsService,
    request_fingerprint,
)


@dataclass(frozen=True, slots=True)
class AssetInput:
    role: BrandAssetRole
    media_asset_id: UUID


@dataclass(frozen=True, slots=True)
class AudienceInput:
    name: str
    description: str | None
    age_min: int | None
    age_max: int | None
    locations: list[str]
    interests: list[str]


@dataclass(frozen=True, slots=True)
class BrandProfileInput:
    display_name: str
    tone: str
    communication_language: str
    default_currency: str
    font_preference: str | None
    legal_footnote: str | None
    color_palette: list[str]
    forbidden_topics: list[str]
    assets: list[AssetInput]
    target_audiences: list[AudienceInput]
    approved_claims: list[str]
    forbidden_claims: list[str]
    approved_ctas: list[str]


@dataclass(frozen=True, slots=True)
class PriceInput:
    price_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class ProductInput:
    name: str
    category: str | None
    description: str | None
    status: ProductStatus
    stock_status: StockStatus
    valid_locations: list[str]
    landing_page_url: str | None
    price: PriceInput | None


@dataclass(frozen=True, slots=True)
class ProductPatch:
    name: str | None
    category: str | None
    description: str | None
    status: ProductStatus | None
    stock_status: StockStatus | None
    valid_locations: list[str] | None
    landing_page_url: str | None
    price: PriceInput | None


@dataclass(frozen=True, slots=True)
class CampaignOfferInput:
    name: str
    status: CampaignOfferStatus
    approval_status: CampaignApprovalStatus
    starts_at: datetime
    ends_at: datetime
    discount_type: DiscountType
    discount_percent: int | None
    discount_amount_minor: int | None
    discount_currency: str | None
    product_ids: list[UUID]
    valid_locations: list[str]
    stock_limit: int | None
    coupon_code: str | None
    legal_text: str | None


@dataclass(frozen=True, slots=True)
class BrandDocument:
    """One read of the whole brand identity: profile, assets, audiences, safety lists."""

    profile: BrandProfile
    assets: tuple[BrandAsset, ...]
    audiences: tuple[TargetAudience, ...]
    approved_claims: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    approved_ctas: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProductView:
    """A product and the price row that is in force now, resolved together."""

    product: Product
    current_price: ProductPrice | None


@dataclass(frozen=True, slots=True)
class CampaignOfferView:
    offer: CampaignOffer
    product_ids: tuple[UUID, ...]
    activity: CampaignActivity

    @property
    def is_active(self) -> bool:
        return self.activity is CampaignActivity.ACTIVE


class BrandService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repository = BrandRepository(session)
        self._businesses = BusinessRepository(session)
        self._media: MediaAssetPort = MediaAssetReader(session)

    # --- brand document ------------------------------------------------------------------

    async def get_brand(self, *, user_id: UUID, business_id: UUID) -> BrandDocument:
        await self._authorize(user_id, business_id, BrandAction.BRAND_READ)
        profile = await self._repository.get_profile(business_id)
        if profile is None:
            raise self._not_found(
                "BRAND_PROFILE_NOT_FOUND", "Brand profile not found", "No brand profile is set."
            )
        return await self._load_document(business_id, profile)

    async def replace_brand(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        payload: BrandProfileInput,
        correlation_id: str,
    ) -> BrandDocument:
        """Replace the brand identity wholesale.

        `PUT` is the honest verb here: the brand identity is one document, and replacing it
        makes the operation naturally idempotent — the same body applied twice leaves the same
        state, so no idempotency key is needed to make a retry safe.
        """

        cleaned = self._clean_profile(payload)
        async with self._session.begin():
            await self._authorize(user_id, business_id, BrandAction.BRAND_WRITE)
            await self._require_active_business(business_id)
            await self._assert_assets_are_tenant_media(business_id, cleaned.assets)
            profile = await self._repository.get_profile(business_id, lock=True)
            if profile is None:
                profile = BrandProfile(id=uuid4(), business_id=business_id)
                self._repository.add(profile)
            for column, value in _profile_columns(cleaned).items():
                setattr(profile, column, value)
            await self._session.flush()
            await self._assert_currency_still_matches_catalogue(business_id, cleaned)
            await self._replace_children(business_id, profile, cleaned)
            await self._session.flush()
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="brand.profile.replaced",
                resource_type="brand_profile",
                resource_id=profile.id,
                correlation_id=correlation_id,
                details={"assets": len(cleaned.assets), "audiences": len(cleaned.target_audiences)},
            )
            return await self._load_document(business_id, profile)

    async def brand_health(self, *, user_id: UUID, business_id: UUID) -> BrandHealth:
        """Advisory score only: this is a read, so it cannot block anything by construction."""

        await self._authorize(user_id, business_id, BrandAction.BRAND_READ)
        profile = await self._repository.get_profile(business_id)
        inventory = await self._media.media_inventory(business_id)
        snapshot = BrandHealthSnapshot(
            has_profile=profile is not None,
            has_display_name=bool(profile and profile.display_name),
            has_tone=bool(profile and profile.tone),
            has_language=bool(profile and profile.communication_language),
            color_count=len(profile.color_palette) if profile else 0,
            logo_count=await self._repository.count_assets_by_role(
                business_id, BrandAssetRole.LOGO
            ),
            active_product_count=await self._repository.count_products(
                business_id, status=ProductStatus.ACTIVE
            ),
            audience_count=await self._repository.count_audiences(business_id),
            forbidden_claim_count=await self._repository.count_text_entries(
                business_id, ForbiddenClaim
            ),
            approved_cta_count=await self._repository.count_text_entries(business_id, ApprovedCta),
            photo_count=inventory.photo_count,
            video_count=inventory.video_count,
            campaign_offer_count=await self._repository.count_campaign_offers(business_id),
        )
        return evaluate_brand_health(snapshot)

    # --- products ------------------------------------------------------------------------

    async def list_products(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        cursor: Cursor | None,
        limit: int | None,
        status: ProductStatus | None,
    ) -> Page[ProductView]:
        await self._authorize(user_id, business_id, BrandAction.CATALOG_READ)
        page_size = resolve_limit(limit)
        rows = await self._repository.list_products(
            business_id, cursor=cursor, limit=page_size, status=status
        )
        prices = await self._repository.current_prices(
            business_id, [product.id for product in rows]
        )
        views = [ProductView(product=row, current_price=prices.get(row.id)) for row in rows]
        return build_page(
            views, limit=page_size, key=lambda view: (view.product.created_at, view.product.id)
        )

    async def create_product(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        payload: ProductInput,
        idempotency_key: str | None,
        correlation_id: str,
    ) -> ProductView:
        cleaned = self._clean_product(payload)
        async with self._session.begin():
            await self._authorize(user_id, business_id, BrandAction.CATALOG_WRITE)
            await self._require_active_business(business_id)
            replay = await self._begin_idempotent(
                business_id=business_id,
                user_id=user_id,
                operation="brand.product.create",
                key=idempotency_key,
                payload=_product_fingerprint(cleaned),
                correlation_id=correlation_id,
            )
            if replay is not None and replay.product_id is not None:
                return await self._product_view(business_id, replay.product_id)
            normalized_name = lookup_key(cleaned.name)
            if await self._repository.product_name_taken(business_id, normalized_name):
                raise self._product_name_conflict()
            product = Product(
                id=uuid4(),
                business_id=business_id,
                name=cleaned.name,
                normalized_name=normalized_name,
                category=cleaned.category,
                description=cleaned.description,
                status=cleaned.status,
                stock_status=cleaned.stock_status,
                valid_locations=cleaned.valid_locations,
                landing_page_url=cleaned.landing_page_url,
            )
            self._repository.add(product)
            try:
                await self._session.flush()
            except IntegrityError as error:
                raise self._product_name_conflict() from error
            price: ProductPrice | None = None
            if cleaned.price is not None:
                price = await self._append_price(
                    business_id=business_id, product=product, requested=cleaned.price, current=None
                )
                await self._session.flush()
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="brand.product.created",
                resource_type="product",
                resource_id=product.id,
                correlation_id=correlation_id,
                details={"priced": price is not None},
            )
            await self._complete_idempotent(
                replay, response_status=201, body={"product_id": str(product.id)}
            )
            return ProductView(product=product, current_price=price)

    async def update_product(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        product_id: UUID,
        payload: ProductPatch,
        correlation_id: str,
    ) -> ProductView:
        cleaned = self._clean_product_patch(payload)
        async with self._session.begin():
            await self._authorize(user_id, business_id, BrandAction.CATALOG_WRITE)
            await self._require_active_business(business_id)
            product = await self._repository.get_product(business_id, product_id, lock=True)
            if product is None:
                raise self._not_found(
                    "PRODUCT_NOT_FOUND", "Product not found", "The product is not available."
                )
            if cleaned.name is not None:
                normalized_name = lookup_key(cleaned.name)
                if await self._repository.product_name_taken(
                    business_id, normalized_name, exclude_id=product.id
                ):
                    raise self._product_name_conflict()
                product.name, product.normalized_name = cleaned.name, normalized_name
            for column in ("category", "description", "status", "stock_status", "valid_locations"):
                value = getattr(cleaned, column)
                if value is not None:
                    setattr(product, column, value)
            if cleaned.landing_page_url is not None:
                product.landing_page_url = cleaned.landing_page_url
            price = await self._repository.current_price(business_id, product.id, lock=True)
            if cleaned.price is not None:
                price = await self._append_price(
                    business_id=business_id, product=product, requested=cleaned.price, current=price
                )
            await self._session.flush()
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="brand.product.updated",
                resource_type="product",
                resource_id=product.id,
                correlation_id=correlation_id,
                details={"repriced": cleaned.price is not None},
            )
            return ProductView(product=product, current_price=price)

    # --- campaign offers -----------------------------------------------------------------

    async def list_campaign_offers(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        cursor: Cursor | None,
        limit: int | None,
        active_only: bool,
    ) -> Page[CampaignOfferView]:
        await self._authorize(user_id, business_id, BrandAction.CAMPAIGN_READ)
        page_size = resolve_limit(limit)
        now = datetime.now(UTC)
        rows = await self._repository.list_campaign_offers(
            business_id, cursor=cursor, limit=page_size, active_at=now if active_only else None
        )
        links = await self._repository.campaign_product_ids(
            business_id, [offer.id for offer in rows]
        )
        views = [self._offer_view(offer, links.get(offer.id, []), now) for offer in rows]
        return build_page(
            views, limit=page_size, key=lambda view: (view.offer.created_at, view.offer.id)
        )

    async def create_campaign_offer(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        payload: CampaignOfferInput,
        idempotency_key: str | None,
        correlation_id: str,
    ) -> CampaignOfferView:
        cleaned = self._clean_offer(payload)
        async with self._session.begin():
            await self._authorize(user_id, business_id, BrandAction.CAMPAIGN_WRITE)
            await self._require_active_business(business_id)
            replay = await self._begin_idempotent(
                business_id=business_id,
                user_id=user_id,
                operation="brand.campaign_offer.create",
                key=idempotency_key,
                payload=_offer_fingerprint(cleaned),
                correlation_id=correlation_id,
            )
            if replay is not None and replay.campaign_offer_id is not None:
                return await self._offer_view_by_id(business_id, replay.campaign_offer_id)
            await self._assert_products_exist(business_id, cleaned.product_ids)
            await self._assert_discount_currency(business_id, cleaned)
            offer = CampaignOffer(
                id=uuid4(),
                business_id=business_id,
                name=cleaned.name,
                status=cleaned.status,
                approval_status=cleaned.approval_status,
                starts_at=cleaned.starts_at,
                ends_at=cleaned.ends_at,
                discount_type=cleaned.discount_type,
                discount_percent=cleaned.discount_percent,
                discount_amount_minor=cleaned.discount_amount_minor,
                discount_currency=cleaned.discount_currency,
                valid_locations=cleaned.valid_locations,
                stock_limit=cleaned.stock_limit,
                coupon_code=cleaned.coupon_code,
                legal_text=cleaned.legal_text,
            )
            self._repository.add(offer)
            await self._session.flush()
            for product_id in cleaned.product_ids:
                self._repository.add(
                    CampaignOfferProduct(
                        id=uuid4(),
                        business_id=business_id,
                        campaign_offer_id=offer.id,
                        product_id=product_id,
                    )
                )
            await self._session.flush()
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="brand.campaign_offer.created",
                resource_type="campaign_offer",
                resource_id=offer.id,
                correlation_id=correlation_id,
                details={"products": len(cleaned.product_ids)},
            )
            await self._complete_idempotent(
                replay, response_status=201, body={"campaign_offer_id": str(offer.id)}
            )
            return self._offer_view(offer, cleaned.product_ids, datetime.now(UTC))

    # --- validation ----------------------------------------------------------------------

    def _clean_profile(self, payload: BrandProfileInput) -> BrandProfileInput:
        if len(payload.color_palette) > MAX_COLOR_ENTRIES:
            raise invalid(f"Color palette accepts at most {MAX_COLOR_ENTRIES} colors.")
        if len(payload.target_audiences) > MAX_AUDIENCE_ENTRIES:
            raise invalid(f"At most {MAX_AUDIENCE_ENTRIES} target audiences are accepted.")
        if len(payload.assets) > MAX_ASSET_ENTRIES:
            raise invalid(f"At most {MAX_ASSET_ENTRIES} brand assets are accepted.")
        if sum(1 for asset in payload.assets if asset.role is BrandAssetRole.LOGO) > 1:
            raise invalid("Only one primary logo can be set.")
        seen_assets = {(asset.role, asset.media_asset_id) for asset in payload.assets}
        if len(seen_assets) != len(payload.assets):
            raise invalid("Brand assets must be unique per role.")
        return BrandProfileInput(
            display_name=normalize_text(payload.display_name, field="Brand name", limit=160),
            tone=normalize_text(payload.tone, field="Tone", limit=200),
            communication_language=normalize_language(payload.communication_language),
            default_currency=normalize_currency(payload.default_currency),
            font_preference=(
                normalize_text(payload.font_preference, field="Font preference", limit=120)
                if payload.font_preference is not None
                else None
            ),
            legal_footnote=(
                normalize_text(payload.legal_footnote, field="Legal footnote", limit=2000)
                if payload.legal_footnote is not None
                else None
            ),
            color_palette=[normalize_color(color) for color in payload.color_palette],
            forbidden_topics=normalize_entries(payload.forbidden_topics, field="Forbidden topics"),
            assets=list(payload.assets),
            target_audiences=[self._clean_audience(value) for value in payload.target_audiences],
            approved_claims=normalize_entries(payload.approved_claims, field="Approved claims"),
            forbidden_claims=normalize_entries(payload.forbidden_claims, field="Forbidden claims"),
            approved_ctas=normalize_entries(payload.approved_ctas, field="Approved CTAs"),
        )

    def _clean_audience(self, payload: AudienceInput) -> AudienceInput:
        age_min, age_max = payload.age_min, payload.age_max
        for age in (age_min, age_max):
            if age is not None and not MIN_AGE <= age <= MAX_AGE:
                raise invalid(f"Audience ages must be between {MIN_AGE} and {MAX_AGE}.")
        if age_min is not None and age_max is not None and age_min > age_max:
            raise invalid("Audience minimum age cannot exceed the maximum age.")
        return AudienceInput(
            name=normalize_text(payload.name, field="Audience name", limit=120),
            description=(
                normalize_text(payload.description, field="Audience description", limit=1000)
                if payload.description is not None
                else None
            ),
            age_min=age_min,
            age_max=age_max,
            locations=normalize_entries(
                payload.locations, field="Audience locations", limit=MAX_LOCATION_ENTRIES
            ),
            interests=normalize_entries(payload.interests, field="Audience interests"),
        )

    def _clean_product(self, payload: ProductInput) -> ProductInput:
        return ProductInput(
            name=normalize_text(payload.name, field="Product name", limit=160),
            category=(
                normalize_text(payload.category, field="Category", limit=120)
                if payload.category is not None
                else None
            ),
            description=(
                normalize_text(payload.description, field="Description", limit=2000)
                if payload.description is not None
                else None
            ),
            status=payload.status,
            stock_status=payload.stock_status,
            valid_locations=normalize_entries(
                payload.valid_locations, field="Valid locations", limit=MAX_LOCATION_ENTRIES
            ),
            landing_page_url=(
                normalize_url(payload.landing_page_url)
                if payload.landing_page_url is not None
                else None
            ),
            price=self._clean_price(payload.price),
        )

    def _clean_product_patch(self, payload: ProductPatch) -> ProductPatch:
        if all(
            value is None
            for value in (
                payload.name,
                payload.category,
                payload.description,
                payload.status,
                payload.stock_status,
                payload.valid_locations,
                payload.landing_page_url,
                payload.price,
            )
        ):
            raise invalid("No product change was supplied.")
        return ProductPatch(
            name=(
                normalize_text(payload.name, field="Product name", limit=160)
                if payload.name is not None
                else None
            ),
            category=(
                normalize_text(payload.category, field="Category", limit=120)
                if payload.category is not None
                else None
            ),
            description=(
                normalize_text(payload.description, field="Description", limit=2000)
                if payload.description is not None
                else None
            ),
            status=payload.status,
            stock_status=payload.stock_status,
            valid_locations=(
                normalize_entries(
                    payload.valid_locations, field="Valid locations", limit=MAX_LOCATION_ENTRIES
                )
                if payload.valid_locations is not None
                else None
            ),
            landing_page_url=(
                normalize_url(payload.landing_page_url)
                if payload.landing_page_url is not None
                else None
            ),
            price=self._clean_price(payload.price),
        )

    @staticmethod
    def _clean_price(payload: PriceInput | None) -> PriceInput | None:
        if payload is None:
            return None
        return PriceInput(
            price_minor=normalize_price_minor(payload.price_minor),
            currency=normalize_currency(payload.currency),
        )

    def _clean_offer(self, payload: CampaignOfferInput) -> CampaignOfferInput:
        starts_at, ends_at = self._utc(payload.starts_at), self._utc(payload.ends_at)
        if ends_at <= starts_at:
            raise ProblemException(
                status=422,
                code="CAMPAIGN_WINDOW_INVALID",
                title="Invalid campaign window",
                detail="The campaign end must be after its start.",
            )
        if payload.status is CampaignOfferStatus.CANCELLED:
            raise invalid("A campaign cannot be created as cancelled.")
        if payload.approval_status in (
            CampaignApprovalStatus.APPROVED,
            CampaignApprovalStatus.REJECTED,
        ):
            raise invalid("Approval decisions are not part of campaign creation.")
        if payload.stock_limit is not None and not 1 <= payload.stock_limit <= MAX_STOCK_LIMIT:
            raise invalid(f"Stock limit must be between 1 and {MAX_STOCK_LIMIT}.")
        if len(payload.product_ids) != len(set(payload.product_ids)):
            raise invalid("Campaign products must be unique.")
        percent, amount, currency = self._clean_discount(payload)
        return CampaignOfferInput(
            name=normalize_text(payload.name, field="Campaign name", limit=160),
            status=payload.status,
            approval_status=payload.approval_status,
            starts_at=starts_at,
            ends_at=ends_at,
            discount_type=payload.discount_type,
            discount_percent=percent,
            discount_amount_minor=amount,
            discount_currency=currency,
            product_ids=list(payload.product_ids),
            valid_locations=normalize_entries(
                payload.valid_locations, field="Valid locations", limit=MAX_LOCATION_ENTRIES
            ),
            stock_limit=payload.stock_limit,
            coupon_code=(
                normalize_text(payload.coupon_code, field="Coupon code", limit=64)
                if payload.coupon_code is not None
                else None
            ),
            legal_text=(
                normalize_text(payload.legal_text, field="Legal text", limit=4000)
                if payload.legal_text is not None
                else None
            ),
        )

    @staticmethod
    def _clean_discount(payload: CampaignOfferInput) -> tuple[int | None, int | None, str | None]:
        """Exactly the fields the discount type needs; a mixed discount is not representable."""

        if payload.discount_type is DiscountType.PERCENTAGE:
            if payload.discount_percent is None:
                raise invalid("A percentage campaign requires a discount percentage.")
            if payload.discount_amount_minor is not None or payload.discount_currency is not None:
                raise invalid("A percentage campaign cannot carry a fixed amount.")
            if not 1 <= payload.discount_percent <= MAX_DISCOUNT_PERCENT:
                raise invalid(f"Discount percentage must be between 1 and {MAX_DISCOUNT_PERCENT}.")
            return payload.discount_percent, None, None
        if payload.discount_amount_minor is None or payload.discount_currency is None:
            raise invalid(
                "A fixed-amount campaign requires an amount in minor units and a currency."
            )
        if payload.discount_percent is not None:
            raise invalid("A fixed-amount campaign cannot carry a percentage.")
        money = Money(
            amount_minor=normalize_price_minor(payload.discount_amount_minor),
            currency=normalize_currency(payload.discount_currency),
        )
        if money.amount_minor < 1:
            raise invalid("A fixed-amount discount must be greater than zero.")
        return None, money.amount_minor, money.currency

    @staticmethod
    def _utc(value: datetime) -> datetime:
        """Timestamps are stored in UTC; a naive timestamp has no defined instant."""

        if value.tzinfo is None:
            raise invalid("Timestamps must include a timezone offset.")
        return value.astimezone(UTC)

    # --- currency and reference integrity ------------------------------------------------

    async def _append_price(
        self,
        *,
        business_id: UUID,
        product: Product,
        requested: PriceInput,
        current: ProductPrice | None,
    ) -> ProductPrice:
        """Close the open price row and append a new one, keeping the currency stable.

        A product's currency is set by its first price and never changes afterwards: silently
        reinterpreting 16500 from TRY to EUR would corrupt every past quote of that product.
        """

        now = datetime.now(UTC)
        money = Money(amount_minor=requested.price_minor, currency=requested.currency)
        tenant_currency = await self._tenant_currency(business_id)
        if tenant_currency is not None and tenant_currency != money.currency:
            raise self._currency_mismatch(
                "A product price must use the brand's currency of record."
            )
        if current is not None:
            if current.currency != money.currency:
                raise self._currency_mismatch(
                    "A product price cannot change currency after it is set."
                )
            if current.price_minor == money.amount_minor:
                return current
            current.effective_to = now
        price = self._new_price(
            business_id=business_id, product_id=product.id, money=money, effective_from=now
        )
        self._repository.add(price)
        return price

    @staticmethod
    def _new_price(
        *, business_id: UUID, product_id: UUID, money: Money, effective_from: datetime
    ) -> ProductPrice:
        return ProductPrice(
            id=uuid4(),
            business_id=business_id,
            product_id=product_id,
            price_minor=money.amount_minor,
            currency=money.currency,
            effective_from=effective_from,
        )

    async def _tenant_currency(self, business_id: UUID) -> str | None:
        """The brand's currency of record, once a brand profile exists.

        A business operates in one currency; letting one product be priced in TRY and another in
        EUR would leave a generated post unable to state a total. Before a brand profile exists
        the first price is free to set the currency.
        """

        profile = await self._repository.get_profile(business_id)
        return profile.default_currency if profile is not None else None

    async def _assert_currency_still_matches_catalogue(
        self, business_id: UUID, payload: BrandProfileInput
    ) -> None:
        """Changing the brand currency must not orphan prices that were already quoted."""

        open_price = await self._repository.any_price_currency_other_than(
            business_id, payload.default_currency
        )
        if open_price is not None:
            raise self._currency_mismatch(
                "Existing product prices use another currency; reprice the catalogue first."
            )

    async def _assert_products_exist(self, business_id: UUID, product_ids: Sequence[UUID]) -> None:
        """A campaign may only cite products of this tenant; unknown ids are not disclosed."""

        known = await self._repository.existing_product_ids(business_id, product_ids)
        if len(known) != len(set(product_ids)):
            raise ProblemException(
                status=422,
                code="CAMPAIGN_PRODUCT_UNKNOWN",
                title="Unknown campaign product",
                detail="One or more products are not available in this business.",
            )

    async def _assert_discount_currency(self, business_id: UUID, offer: CampaignOfferInput) -> None:
        if offer.discount_currency is None or not offer.product_ids:
            return
        prices = await self._repository.current_prices(business_id, offer.product_ids)
        if any(price.currency != offer.discount_currency for price in prices.values()):
            raise self._currency_mismatch(
                "The discount currency must match the currency of every campaign product."
            )

    async def _assert_assets_are_tenant_media(
        self, business_id: UUID, assets: Sequence[AssetInput]
    ) -> None:
        asset_ids = [asset.media_asset_id for asset in assets]
        usable = await self._media.usable_asset_ids(business_id, asset_ids)
        if len(usable) != len(set(asset_ids)):
            raise ProblemException(
                status=422,
                code="BRAND_ASSET_INVALID",
                title="Invalid brand asset",
                detail="A brand asset must reference an uploaded media asset of this business.",
            )

    # --- persistence helpers -------------------------------------------------------------

    async def _replace_children(
        self, business_id: UUID, profile: BrandProfile, payload: BrandProfileInput
    ) -> None:
        """A `PUT` replaces the brand's lists; leftovers from the previous body must not stay."""

        await self._repository.delete_assets(business_id, profile.id)
        await self._repository.delete_audiences(business_id)
        for model in (ApprovedClaim, ForbiddenClaim, ApprovedCta):
            await self._repository.delete_text_entries(business_id, model)
        for asset in payload.assets:
            self._repository.add(
                BrandAsset(
                    id=uuid4(),
                    business_id=business_id,
                    brand_profile_id=profile.id,
                    media_asset_id=asset.media_asset_id,
                    role=asset.role,
                )
            )
        for audience in payload.target_audiences:
            self._repository.add(
                TargetAudience(
                    id=uuid4(),
                    business_id=business_id,
                    name=audience.name,
                    normalized_name=lookup_key(audience.name),
                    description=audience.description,
                    age_min=audience.age_min,
                    age_max=audience.age_max,
                    locations=audience.locations,
                    interests=audience.interests,
                )
            )
        for value in payload.approved_claims:
            self._repository.add(
                ApprovedClaim(
                    id=uuid4(),
                    business_id=business_id,
                    value=value,
                    lookup_key=lookup_key(value),
                )
            )
        for value in payload.forbidden_claims:
            self._repository.add(
                ForbiddenClaim(
                    id=uuid4(),
                    business_id=business_id,
                    value=value,
                    lookup_key=lookup_key(value),
                )
            )
        for value in payload.approved_ctas:
            self._repository.add(
                ApprovedCta(
                    id=uuid4(), business_id=business_id, value=value, lookup_key=lookup_key(value)
                )
            )

    async def _load_document(self, business_id: UUID, profile: BrandProfile) -> BrandDocument:
        return BrandDocument(
            profile=profile,
            assets=tuple(await self._repository.list_assets(business_id)),
            audiences=tuple(await self._repository.list_audiences(business_id)),
            approved_claims=tuple(
                await self._repository.list_text_entries(business_id, ApprovedClaim)
            ),
            forbidden_claims=tuple(
                await self._repository.list_text_entries(business_id, ForbiddenClaim)
            ),
            approved_ctas=tuple(await self._repository.list_text_entries(business_id, ApprovedCta)),
        )

    async def _product_view(self, business_id: UUID, product_id: UUID) -> ProductView:
        product = await self._repository.get_product(business_id, product_id)
        if product is None:
            raise self._not_found(
                "PRODUCT_NOT_FOUND", "Product not found", "The product is not available."
            )
        return ProductView(
            product=product,
            current_price=await self._repository.current_price(business_id, product_id),
        )

    async def _offer_view_by_id(self, business_id: UUID, offer_id: UUID) -> CampaignOfferView:
        offer = await self._repository.get_campaign_offer(business_id, offer_id)
        if offer is None:
            raise self._not_found(
                "CAMPAIGN_OFFER_NOT_FOUND",
                "Campaign offer not found",
                "The campaign is not available.",
            )
        links = await self._repository.campaign_product_ids(business_id, [offer_id])
        return self._offer_view(offer, links.get(offer_id, []), datetime.now(UTC))

    def _offer_view(
        self, offer: CampaignOffer, product_ids: Sequence[UUID], now: datetime
    ) -> CampaignOfferView:
        return CampaignOfferView(
            offer=offer,
            product_ids=tuple(product_ids),
            activity=evaluate_campaign_activity(
                status=offer.status,
                approval_status=offer.approval_status,
                starts_at=offer.starts_at,
                ends_at=offer.ends_at,
                now=now,
            ),
        )

    # --- authorization, idempotency, audit -----------------------------------------------

    async def _authorize(
        self, user_id: UUID, business_id: UUID, action: BrandAction
    ) -> BusinessMember:
        """Membership first, then permission: an outsider gets `404`, a member gets `403`."""

        membership = await self._businesses.get_active_membership(business_id, user_id)
        if membership is None:
            raise self._not_found(
                "BUSINESS_NOT_FOUND", "Business not found", "The business is not available."
            )
        if not permits_action(membership.role, action):
            raise ProblemException(
                status=403,
                code="INSUFFICIENT_PERMISSION",
                title="Forbidden",
                detail="You do not have this permission.",
            )
        return membership

    async def _require_active_business(self, business_id: UUID) -> None:
        business = await self._businesses.get_business(business_id)
        if business is None:
            raise self._not_found(
                "BUSINESS_NOT_FOUND", "Business not found", "The business is not available."
            )
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
        operation: str,
        key: str | None,
        payload: dict[str, object],
        correlation_id: str,
    ) -> _IdempotentRequest | None:
        if key is None:
            return None
        result = await IdempotencyService(OperationsRepository(self._session)).acquire(
            business_id=business_id,
            actor_user_id=user_id,
            operation=operation,
            key=key,
            fingerprint=request_fingerprint(payload),
            correlation_id=correlation_id,
        )
        body = result.record.response_body or {}
        product_id = body.get("product_id") if result.is_replay else None
        offer_id = body.get("campaign_offer_id") if result.is_replay else None
        return _IdempotentRequest(
            record=result.record,
            product_id=UUID(product_id) if isinstance(product_id, str) else None,
            campaign_offer_id=UUID(offer_id) if isinstance(offer_id, str) else None,
        )

    async def _complete_idempotent(
        self, request: _IdempotentRequest | None, *, response_status: int, body: dict[str, object]
    ) -> None:
        if request is None:
            return
        await OperationsService(self._session, self._settings).complete_idempotency(
            request.record, response_status=response_status, response_body=body
        )

    def _audit(
        self,
        *,
        business_id: UUID,
        user_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        correlation_id: str,
        details: dict[str, object],
    ) -> None:
        """Brand data is what content is allowed to claim; every change names its actor."""

        OperationsRepository(self._session).add(
            AuditLog(
                id=uuid4(),
                business_id=business_id,
                actor_user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                correlation_id=correlation_id,
                details=details,
            )
        )

    @staticmethod
    def _not_found(code: str, title: str, detail: str) -> ProblemException:
        return ProblemException(status=404, code=code, title=title, detail=detail)

    @staticmethod
    def _product_name_conflict() -> ProblemException:
        return ProblemException(
            status=409,
            code="PRODUCT_NAME_CONFLICT",
            title="Product name conflict",
            detail="A product with this name already exists in this business.",
        )

    @staticmethod
    def _currency_mismatch(detail: str) -> ProblemException:
        return ProblemException(
            status=409, code="CURRENCY_MISMATCH", title="Currency mismatch", detail=detail
        )


@dataclass(frozen=True, slots=True)
class _IdempotentRequest:
    """The acquired idempotency row plus the resource a replay should return unchanged."""

    record: IdempotencyKey
    product_id: UUID | None
    campaign_offer_id: UUID | None


def _profile_columns(payload: BrandProfileInput) -> dict[str, object]:
    return {
        "display_name": payload.display_name,
        "tone": payload.tone,
        "communication_language": payload.communication_language,
        "default_currency": payload.default_currency,
        "font_preference": payload.font_preference,
        "legal_footnote": payload.legal_footnote,
        "color_palette": payload.color_palette,
        "forbidden_topics": payload.forbidden_topics,
    }


def _product_fingerprint(payload: ProductInput) -> dict[str, object]:
    return {
        "name": lookup_key(payload.name),
        "status": payload.status.value,
        "stock_status": payload.stock_status.value,
        "price_minor": payload.price.price_minor if payload.price else None,
        "currency": payload.price.currency if payload.price else None,
    }


def _offer_fingerprint(payload: CampaignOfferInput) -> dict[str, object]:
    return {
        "name": lookup_key(payload.name),
        "starts_at": payload.starts_at.isoformat(),
        "ends_at": payload.ends_at.isoformat(),
        "discount_type": payload.discount_type.value,
        "discount_percent": payload.discount_percent,
        "discount_amount_minor": payload.discount_amount_minor,
        "discount_currency": payload.discount_currency,
        "product_ids": sorted(str(value) for value in payload.product_ids),
    }
