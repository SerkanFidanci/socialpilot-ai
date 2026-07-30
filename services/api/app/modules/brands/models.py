"""Brand profile, product catalogue, campaign and content-safety persistence models.

Money is stored as an integer count of minor units next to its ISO-4217 currency, never as a
float or a decimal string: a price is a count, not a measurement. There is deliberately no
column of a floating type in this module — `tests/unit/test_brand_catalog_unit.py` asserts it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.identity.models import Base


class ProductStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class StockStatus(StrEnum):
    AVAILABLE = "available"
    LIMITED = "limited"
    OUT_OF_STOCK = "out_of_stock"


class CampaignOfferStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    CANCELLED = "cancelled"


class CampaignApprovalStatus(StrEnum):
    """`not_required` is the honest default until the approval workflow module exists."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DiscountType(StrEnum):
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"


class BrandAssetRole(StrEnum):
    LOGO = "logo"
    LOGO_ALTERNATE = "logo_alternate"
    STYLE_EXAMPLE = "style_example"
    DISLIKED_EXAMPLE = "disliked_example"


def _enum(enum_type: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_type, name=name, values_callable=lambda values: [item.value for item in values]
    )


def _business_id() -> Mapped[UUID]:
    return mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )


class BrandProfile(Base):
    """One brand identity per business; the tenant may not hold two competing identities."""

    __tablename__ = "brand_profiles"
    __table_args__ = (UniqueConstraint("business_id", name="uq_brand_profile_business"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    tone: Mapped[str] = mapped_column(String(200), nullable=False)
    communication_language: Mapped[str] = mapped_column(String(16), nullable=False)
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    font_preference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    legal_footnote: Mapped[str | None] = mapped_column(Text, nullable=True)
    color_palette: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    forbidden_topics: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BrandAsset(Base):
    """A brand asset is a reference to an existing media asset, never a second upload path."""

    __tablename__ = "brand_assets"
    __table_args__ = (
        UniqueConstraint(
            "brand_profile_id", "media_asset_id", "role", name="uq_brand_asset_role_media"
        ),
        Index("ix_brand_assets_business_role", "business_id", "role"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    brand_profile_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("brand_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    media_asset_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[BrandAssetRole] = mapped_column(_enum(BrandAssetRole, "brand_asset_role"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TargetAudience(Base):
    __tablename__ = "target_audiences"
    __table_args__ = (
        UniqueConstraint("business_id", "normalized_name", name="uq_target_audience_name"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    age_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    age_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    locations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    interests: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("business_id", "normalized_name", name="uq_product_name"),
        Index("ix_products_business_status", "business_id", "status"),
        Index("ix_products_business_created", "business_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProductStatus] = mapped_column(_enum(ProductStatus, "product_status"))
    stock_status: Mapped[StockStatus] = mapped_column(_enum(StockStatus, "product_stock_status"))
    valid_locations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    landing_page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProductPrice(Base):
    """Append-only price history: the price a generated post quoted stays reconstructable.

    `price_minor` is an integer count of minor units (16500 = ₺165,00) and travels with the
    currency it is counted in. A price is never updated in place; a change closes the current
    row and appends a new one, so `effective_from`/`effective_to` answer "what was the verified
    price at time T" deterministically.
    """

    __tablename__ = "product_prices"
    __table_args__ = (
        Index("ix_product_prices_product_effective", "business_id", "product_id", "effective_from"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    product_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CampaignOffer(Base):
    """The verified campaign record. Generation reads dates and discounts only from here."""

    __tablename__ = "campaign_offers"
    __table_args__ = (
        Index("ix_campaign_offers_business_window", "business_id", "starts_at", "ends_at"),
        Index("ix_campaign_offers_business_created", "business_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[CampaignOfferStatus] = mapped_column(
        _enum(CampaignOfferStatus, "campaign_offer_status")
    )
    approval_status: Mapped[CampaignApprovalStatus] = mapped_column(
        _enum(CampaignApprovalStatus, "campaign_approval_status")
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    discount_type: Mapped[DiscountType] = mapped_column(_enum(DiscountType, "discount_type"))
    discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    discount_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    valid_locations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    stock_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coupon_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    legal_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CampaignOfferProduct(Base):
    """Campaign-to-product link with foreign keys, so a campaign cannot cite a deleted product."""

    __tablename__ = "campaign_offer_products"
    __table_args__ = (
        UniqueConstraint("campaign_offer_id", "product_id", name="uq_campaign_offer_product"),
        Index("ix_campaign_offer_products_business_offer", "business_id", "campaign_offer_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    campaign_offer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("campaign_offers.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApprovedClaim(Base):
    """A statement the business has verified and permits content to make."""

    __tablename__ = "approved_claims"
    __table_args__ = (UniqueConstraint("business_id", "lookup_key", name="uq_approved_claim"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    value: Mapped[str] = mapped_column(String(300), nullable=False)
    lookup_key: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ForbiddenClaim(Base):
    """A statement content must never make, regardless of what a model proposes."""

    __tablename__ = "forbidden_claims"
    __table_args__ = (UniqueConstraint("business_id", "lookup_key", name="uq_forbidden_claim"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    value: Mapped[str] = mapped_column(String(300), nullable=False)
    lookup_key: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApprovedCta(Base):
    """A call to action the business permits; generation may not invent one."""

    __tablename__ = "approved_ctas"
    __table_args__ = (UniqueConstraint("business_id", "lookup_key", name="uq_approved_cta"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    value: Mapped[str] = mapped_column(String(300), nullable=False)
    lookup_key: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
