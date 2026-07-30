"""Brand, catalogue and campaign HTTP transport only; every rule lives in the service."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.config import Settings
from app.core.correlation import get_correlation_id
from app.core.pagination import MAX_PAGE_SIZE, decode_cursor
from app.infrastructure.database.session import get_session
from app.modules.brands.domain import (
    MAX_ASSET_ENTRIES,
    MAX_AUDIENCE_ENTRIES,
    MAX_COLOR_ENTRIES,
    MAX_LIST_ENTRIES,
    MAX_LOCATION_ENTRIES,
    MAX_TEXT_ENTRY_LENGTH,
    BrandHealth,
)
from app.modules.brands.models import (
    BrandAssetRole,
    CampaignApprovalStatus,
    CampaignOfferStatus,
    DiscountType,
    ProductStatus,
    StockStatus,
)
from app.modules.brands.service import (
    AssetInput,
    AudienceInput,
    BrandDocument,
    BrandProfileInput,
    BrandService,
    CampaignOfferInput,
    CampaignOfferView,
    PriceInput,
    ProductInput,
    ProductPatch,
    ProductView,
)
from app.modules.identity.models import User

router = APIRouter(prefix="/v1", tags=["brands"])

# A claim or call to action is one bounded line of text; the bound is in the schema so an
# oversized entry is rejected before any rule runs.
TextEntry = Annotated[str, Field(min_length=1, max_length=MAX_TEXT_ENTRY_LENGTH)]


def service(session: AsyncSession, request: Request) -> BrandService:
    return BrandService(session, cast(Settings, request.app.state.settings))


def correlation() -> str:
    return get_correlation_id() or "unknown"


class BrandAssetPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: BrandAssetRole
    media_asset_id: UUID


class AudiencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    age_min: int | None = Field(default=None, ge=0, le=200)
    age_max: int | None = Field(default=None, ge=0, le=200)
    locations: list[str] = Field(default_factory=list, max_length=MAX_LOCATION_ENTRIES)
    interests: list[str] = Field(default_factory=list, max_length=MAX_LIST_ENTRIES)


class BrandRequest(BaseModel):
    """A brand identity is one document; `PUT` replaces it whole."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=160)
    tone: str = Field(min_length=1, max_length=200)
    communication_language: str = Field(min_length=2, max_length=16)
    default_currency: str = Field(min_length=3, max_length=3)
    font_preference: str | None = Field(default=None, max_length=120)
    legal_footnote: str | None = Field(default=None, max_length=2000)
    color_palette: list[str] = Field(default_factory=list, max_length=MAX_COLOR_ENTRIES)
    forbidden_topics: list[str] = Field(default_factory=list, max_length=MAX_LIST_ENTRIES)
    assets: list[BrandAssetPayload] = Field(default_factory=list, max_length=MAX_ASSET_ENTRIES)
    target_audiences: list[AudiencePayload] = Field(
        default_factory=list, max_length=MAX_AUDIENCE_ENTRIES
    )
    approved_claims: list[TextEntry] = Field(default_factory=list, max_length=MAX_LIST_ENTRIES)
    forbidden_claims: list[TextEntry] = Field(default_factory=list, max_length=MAX_LIST_ENTRIES)
    approved_ctas: list[TextEntry] = Field(default_factory=list, max_length=MAX_LIST_ENTRIES)

    def to_input(self) -> BrandProfileInput:
        return BrandProfileInput(
            display_name=self.display_name,
            tone=self.tone,
            communication_language=self.communication_language,
            default_currency=self.default_currency,
            font_preference=self.font_preference,
            legal_footnote=self.legal_footnote,
            color_palette=list(self.color_palette),
            forbidden_topics=list(self.forbidden_topics),
            assets=[
                AssetInput(role=asset.role, media_asset_id=asset.media_asset_id)
                for asset in self.assets
            ],
            target_audiences=[
                AudienceInput(
                    name=audience.name,
                    description=audience.description,
                    age_min=audience.age_min,
                    age_max=audience.age_max,
                    locations=list(audience.locations),
                    interests=list(audience.interests),
                )
                for audience in self.target_audiences
            ],
            approved_claims=list(self.approved_claims),
            forbidden_claims=list(self.forbidden_claims),
            approved_ctas=list(self.approved_ctas),
        )


class BrandAssetResponse(BaseModel):
    role: BrandAssetRole
    media_asset_id: UUID


class AudienceResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    age_min: int | None
    age_max: int | None
    locations: list[str]
    interests: list[str]


class BrandResponse(BaseModel):
    id: UUID
    business_id: UUID
    display_name: str
    tone: str
    communication_language: str
    default_currency: str
    font_preference: str | None
    legal_footnote: str | None
    color_palette: list[str]
    forbidden_topics: list[str]
    assets: list[BrandAssetResponse]
    target_audiences: list[AudienceResponse]
    approved_claims: list[str]
    forbidden_claims: list[str]
    approved_ctas: list[str]
    updated_at: datetime

    @classmethod
    def make(cls, document: BrandDocument) -> BrandResponse:
        profile = document.profile
        return cls(
            id=profile.id,
            business_id=profile.business_id,
            display_name=profile.display_name,
            tone=profile.tone,
            communication_language=profile.communication_language,
            default_currency=profile.default_currency,
            font_preference=profile.font_preference,
            legal_footnote=profile.legal_footnote,
            color_palette=list(profile.color_palette),
            forbidden_topics=list(profile.forbidden_topics),
            assets=[
                BrandAssetResponse(role=asset.role, media_asset_id=asset.media_asset_id)
                for asset in document.assets
            ],
            target_audiences=[
                AudienceResponse(
                    id=audience.id,
                    name=audience.name,
                    description=audience.description,
                    age_min=audience.age_min,
                    age_max=audience.age_max,
                    locations=list(audience.locations),
                    interests=list(audience.interests),
                )
                for audience in document.audiences
            ],
            approved_claims=list(document.approved_claims),
            forbidden_claims=list(document.forbidden_claims),
            approved_ctas=list(document.approved_ctas),
            updated_at=profile.updated_at,
        )


class HealthComponentResponse(BaseModel):
    key: str
    status: str
    detail: str


class BrandHealthResponse(BaseModel):
    """Advisory only: the score never blocks a write, and the client is told so explicitly."""

    score: int
    advisory: bool
    components: list[HealthComponentResponse]
    missing: list[str]
    unavailable: list[str]

    @classmethod
    def make(cls, health: BrandHealth) -> BrandHealthResponse:
        return cls(
            score=health.score,
            advisory=health.advisory,
            components=[
                HealthComponentResponse(
                    key=component.key, status=component.status.value, detail=component.detail
                )
                for component in health.components
            ],
            missing=list(health.missing_keys),
            unavailable=list(health.unavailable_keys),
        )


class PricePayload(BaseModel):
    """Money crosses the wire as an integer count of minor units, never as a decimal."""

    model_config = ConfigDict(extra="forbid")

    price_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


class ProductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    category: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    status: ProductStatus = ProductStatus.ACTIVE
    stock_status: StockStatus = StockStatus.AVAILABLE
    valid_locations: list[str] = Field(default_factory=list, max_length=MAX_LOCATION_ENTRIES)
    landing_page_url: str | None = Field(default=None, max_length=2048)
    price: PricePayload | None = None

    def to_input(self) -> ProductInput:
        return ProductInput(
            name=self.name,
            category=self.category,
            description=self.description,
            status=self.status,
            stock_status=self.stock_status,
            valid_locations=list(self.valid_locations),
            landing_page_url=self.landing_page_url,
            price=_price_input(self.price),
        )


class ProductPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    category: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    status: ProductStatus | None = None
    stock_status: StockStatus | None = None
    valid_locations: list[str] | None = Field(default=None, max_length=MAX_LOCATION_ENTRIES)
    landing_page_url: str | None = Field(default=None, max_length=2048)
    price: PricePayload | None = None

    def to_input(self) -> ProductPatch:
        return ProductPatch(
            name=self.name,
            category=self.category,
            description=self.description,
            status=self.status,
            stock_status=self.stock_status,
            valid_locations=list(self.valid_locations)
            if self.valid_locations is not None
            else None,
            landing_page_url=self.landing_page_url,
            price=_price_input(self.price),
        )


class ProductResponse(BaseModel):
    id: UUID
    business_id: UUID
    name: str
    category: str | None
    description: str | None
    status: ProductStatus
    stock_status: StockStatus
    valid_locations: list[str]
    landing_page_url: str | None
    price_minor: int | None
    currency: str | None
    price_effective_from: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def make(cls, view: ProductView) -> ProductResponse:
        product, price = view.product, view.current_price
        return cls(
            id=product.id,
            business_id=product.business_id,
            name=product.name,
            category=product.category,
            description=product.description,
            status=product.status,
            stock_status=product.stock_status,
            valid_locations=list(product.valid_locations),
            landing_page_url=product.landing_page_url,
            price_minor=price.price_minor if price is not None else None,
            currency=price.currency if price is not None else None,
            price_effective_from=price.effective_from if price is not None else None,
            created_at=product.created_at,
            updated_at=product.updated_at,
        )


class ProductPageResponse(BaseModel):
    items: list[ProductResponse]
    next_cursor: str | None
    has_more: bool


class CampaignOfferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    status: CampaignOfferStatus = CampaignOfferStatus.ACTIVE
    approval_status: CampaignApprovalStatus = CampaignApprovalStatus.NOT_REQUIRED
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    discount_type: DiscountType
    discount_percent: int | None = Field(default=None, ge=0, le=100)
    discount_amount_minor: int | None = Field(default=None, ge=0)
    discount_currency: str | None = Field(default=None, min_length=3, max_length=3)
    product_ids: list[UUID] = Field(default_factory=list, max_length=MAX_LIST_ENTRIES)
    valid_locations: list[str] = Field(default_factory=list, max_length=MAX_LOCATION_ENTRIES)
    stock_limit: int | None = Field(default=None, ge=1)
    coupon_code: str | None = Field(default=None, max_length=64)
    legal_text: str | None = Field(default=None, max_length=4000)

    def to_input(self) -> CampaignOfferInput:
        return CampaignOfferInput(
            name=self.name,
            status=self.status,
            approval_status=self.approval_status,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            discount_type=self.discount_type,
            discount_percent=self.discount_percent,
            discount_amount_minor=self.discount_amount_minor,
            discount_currency=self.discount_currency,
            product_ids=list(self.product_ids),
            valid_locations=list(self.valid_locations),
            stock_limit=self.stock_limit,
            coupon_code=self.coupon_code,
            legal_text=self.legal_text,
        )


class CampaignOfferResponse(BaseModel):
    id: UUID
    business_id: UUID
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
    is_active: bool
    activity: str
    created_at: datetime

    @classmethod
    def make(cls, view: CampaignOfferView) -> CampaignOfferResponse:
        offer = view.offer
        return cls(
            id=offer.id,
            business_id=offer.business_id,
            name=offer.name,
            status=offer.status,
            approval_status=offer.approval_status,
            starts_at=offer.starts_at,
            ends_at=offer.ends_at,
            discount_type=offer.discount_type,
            discount_percent=offer.discount_percent,
            discount_amount_minor=offer.discount_amount_minor,
            discount_currency=offer.discount_currency,
            product_ids=list(view.product_ids),
            valid_locations=list(offer.valid_locations),
            stock_limit=offer.stock_limit,
            coupon_code=offer.coupon_code,
            legal_text=offer.legal_text,
            is_active=view.is_active,
            activity=view.activity.value,
            created_at=offer.created_at,
        )


class CampaignOfferPageResponse(BaseModel):
    items: list[CampaignOfferResponse]
    next_cursor: str | None
    has_more: bool


def _price_input(payload: PricePayload | None) -> PriceInput | None:
    if payload is None:
        return None
    return PriceInput(price_minor=payload.price_minor, currency=payload.currency)


@router.get("/businesses/{business_id}/brand", response_model=BrandResponse)
async def get_brand(
    business_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BrandResponse:
    document = await service(session, request).get_brand(user_id=user.id, business_id=business_id)
    return BrandResponse.make(document)


@router.put("/businesses/{business_id}/brand", response_model=BrandResponse)
async def replace_brand(
    business_id: UUID,
    payload: BrandRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BrandResponse:
    document = await service(session, request).replace_brand(
        user_id=user.id,
        business_id=business_id,
        payload=payload.to_input(),
        correlation_id=correlation(),
    )
    return BrandResponse.make(document)


@router.get("/businesses/{business_id}/brand/health", response_model=BrandHealthResponse)
async def brand_health(
    business_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BrandHealthResponse:
    health = await service(session, request).brand_health(user_id=user.id, business_id=business_id)
    return BrandHealthResponse.make(health)


@router.get("/businesses/{business_id}/products", response_model=ProductPageResponse)
async def list_products(
    business_id: UUID,
    request: Request,
    cursor: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    status: ProductStatus | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProductPageResponse:
    page = await service(session, request).list_products(
        user_id=user.id,
        business_id=business_id,
        cursor=decode_cursor(cursor),
        limit=limit,
        status=status,
    )
    return ProductPageResponse(
        items=[ProductResponse.make(view) for view in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.post("/businesses/{business_id}/products", response_model=ProductResponse, status_code=201)
async def create_product(
    business_id: UUID,
    payload: ProductRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=255),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProductResponse:
    view = await service(session, request).create_product(
        user_id=user.id,
        business_id=business_id,
        payload=payload.to_input(),
        idempotency_key=idempotency_key,
        correlation_id=correlation(),
    )
    return ProductResponse.make(view)


@router.patch("/businesses/{business_id}/products/{product_id}", response_model=ProductResponse)
async def update_product(
    business_id: UUID,
    product_id: UUID,
    payload: ProductPatchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProductResponse:
    view = await service(session, request).update_product(
        user_id=user.id,
        business_id=business_id,
        product_id=product_id,
        payload=payload.to_input(),
        correlation_id=correlation(),
    )
    return ProductResponse.make(view)


@router.get("/businesses/{business_id}/campaign-offers", response_model=CampaignOfferPageResponse)
async def list_campaign_offers(
    business_id: UUID,
    request: Request,
    cursor: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    active_only: bool = Query(default=False),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CampaignOfferPageResponse:
    page = await service(session, request).list_campaign_offers(
        user_id=user.id,
        business_id=business_id,
        cursor=decode_cursor(cursor),
        limit=limit,
        active_only=active_only,
    )
    return CampaignOfferPageResponse(
        items=[CampaignOfferResponse.make(view) for view in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.post(
    "/businesses/{business_id}/campaign-offers",
    response_model=CampaignOfferResponse,
    status_code=201,
)
async def create_campaign_offer(
    business_id: UUID,
    payload: CampaignOfferRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=255),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CampaignOfferResponse:
    view = await service(session, request).create_campaign_offer(
        user_id=user.id,
        business_id=business_id,
        payload=payload.to_input(),
        idempotency_key=idempotency_key,
        correlation_id=correlation(),
    )
    return CampaignOfferResponse.make(view)
