"""Tenant-scoped brand, catalogue and campaign persistence.

Every method takes `business_id` and constrains its statement with it. There is no
general-purpose `list_all()`: a query that can be written without a tenant filter will
eventually be called without one.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import ColumnElement, Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Cursor, apply_cursor, fetch_size
from app.modules.brands.domain import MediaInventory
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
    ForbiddenClaim,
    Product,
    ProductPrice,
    ProductStatus,
    TargetAudience,
)
from app.modules.media.models import IngestStatus, MediaAsset, MediaAssetStatus

BrandRecord = (
    ApprovedClaim
    | ApprovedCta
    | BrandAsset
    | BrandProfile
    | CampaignOffer
    | CampaignOfferProduct
    | ForbiddenClaim
    | Product
    | ProductPrice
    | TargetAudience
)

TextListModel = type[ApprovedClaim] | type[ApprovedCta] | type[ForbiddenClaim]


class BrandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, value: BrandRecord) -> None:
        self._session.add(value)

    # --- brand profile -------------------------------------------------------------------

    async def get_profile(self, business_id: UUID, *, lock: bool = False) -> BrandProfile | None:
        statement = select(BrandProfile).where(BrandProfile.business_id == business_id)
        if lock:
            statement = statement.with_for_update()
        return cast(BrandProfile | None, await self._session.scalar(statement))

    async def list_assets(self, business_id: UUID) -> list[BrandAsset]:
        statement: Select[tuple[BrandAsset]] = (
            select(BrandAsset)
            .where(BrandAsset.business_id == business_id)
            .order_by(BrandAsset.role, BrandAsset.created_at, BrandAsset.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def delete_assets(self, business_id: UUID, profile_id: UUID) -> None:
        await self._session.execute(
            delete(BrandAsset).where(
                BrandAsset.business_id == business_id, BrandAsset.brand_profile_id == profile_id
            )
        )

    async def count_assets_by_role(self, business_id: UUID, role: BrandAssetRole) -> int:
        return await self._count(
            select(func.count())
            .select_from(BrandAsset)
            .where(BrandAsset.business_id == business_id, BrandAsset.role == role)
        )

    # --- target audiences ----------------------------------------------------------------

    async def list_audiences(self, business_id: UUID) -> list[TargetAudience]:
        statement: Select[tuple[TargetAudience]] = (
            select(TargetAudience)
            .where(TargetAudience.business_id == business_id)
            .order_by(TargetAudience.name, TargetAudience.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def delete_audiences(self, business_id: UUID) -> None:
        await self._session.execute(
            delete(TargetAudience).where(TargetAudience.business_id == business_id)
        )

    async def count_audiences(self, business_id: UUID) -> int:
        return await self._count(
            select(func.count())
            .select_from(TargetAudience)
            .where(TargetAudience.business_id == business_id)
        )

    # --- content-safety lists ------------------------------------------------------------

    async def list_text_entries(self, business_id: UUID, model: TextListModel) -> list[str]:
        statement = (
            select(model.value)
            .where(model.business_id == business_id)
            .order_by(model.value, model.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def delete_text_entries(self, business_id: UUID, model: TextListModel) -> None:
        await self._session.execute(delete(model).where(model.business_id == business_id))

    async def count_text_entries(self, business_id: UUID, model: TextListModel) -> int:
        return await self._count(
            select(func.count()).select_from(model).where(model.business_id == business_id)
        )

    # --- products ------------------------------------------------------------------------

    async def get_product(
        self, business_id: UUID, product_id: UUID, *, lock: bool = False
    ) -> Product | None:
        statement = select(Product).where(
            Product.business_id == business_id, Product.id == product_id
        )
        if lock:
            statement = statement.with_for_update()
        return cast(Product | None, await self._session.scalar(statement))

    async def product_name_taken(
        self, business_id: UUID, normalized_name: str, *, exclude_id: UUID | None = None
    ) -> bool:
        statement = select(Product.id).where(
            Product.business_id == business_id, Product.normalized_name == normalized_name
        )
        if exclude_id is not None:
            statement = statement.where(Product.id != exclude_id)
        return await self._session.scalar(statement) is not None

    async def list_products(
        self,
        business_id: UUID,
        *,
        cursor: Cursor | None,
        limit: int,
        status: ProductStatus | None = None,
    ) -> list[Product]:
        """Return at most `limit + 1` rows so the caller can detect a next page."""

        statement: Select[tuple[Product]] = select(Product).where(
            Product.business_id == business_id
        )
        if status is not None:
            statement = statement.where(Product.status == status)
        paged = apply_cursor(
            statement, created_at=Product.created_at, identifier=Product.id, cursor=cursor
        ).limit(fetch_size(limit))
        return list((await self._session.scalars(paged)).all())

    async def count_products(
        self, business_id: UUID, *, status: ProductStatus | None = None
    ) -> int:
        statement = (
            select(func.count()).select_from(Product).where(Product.business_id == business_id)
        )
        if status is not None:
            statement = statement.where(Product.status == status)
        return await self._count(statement)

    async def existing_product_ids(
        self, business_id: UUID, product_ids: Sequence[UUID]
    ) -> frozenset[UUID]:
        if not product_ids:
            return frozenset()
        statement = select(Product.id).where(
            Product.business_id == business_id, Product.id.in_(tuple(product_ids))
        )
        return frozenset((await self._session.scalars(statement)).all())

    # --- product prices ------------------------------------------------------------------

    async def current_price(
        self, business_id: UUID, product_id: UUID, *, lock: bool = False
    ) -> ProductPrice | None:
        """The open price row. Exactly one row per product has `effective_to IS NULL`."""

        statement = select(ProductPrice).where(
            ProductPrice.business_id == business_id,
            ProductPrice.product_id == product_id,
            ProductPrice.effective_to.is_(None),
        )
        if lock:
            statement = statement.with_for_update()
        return cast(ProductPrice | None, await self._session.scalar(statement))

    async def current_prices(
        self, business_id: UUID, product_ids: Sequence[UUID]
    ) -> dict[UUID, ProductPrice]:
        if not product_ids:
            return {}
        statement: Select[tuple[ProductPrice]] = select(ProductPrice).where(
            ProductPrice.business_id == business_id,
            ProductPrice.product_id.in_(tuple(product_ids)),
            ProductPrice.effective_to.is_(None),
        )
        return {price.product_id: price for price in (await self._session.scalars(statement)).all()}

    async def any_price_currency_other_than(self, business_id: UUID, currency: str) -> str | None:
        """The first open price whose currency differs, or `None` when the catalogue agrees."""

        statement = select(ProductPrice.currency).where(
            ProductPrice.business_id == business_id,
            ProductPrice.effective_to.is_(None),
            ProductPrice.currency != currency,
        )
        return cast(str | None, await self._session.scalar(statement.limit(1)))

    # --- campaign offers -----------------------------------------------------------------

    async def get_campaign_offer(self, business_id: UUID, offer_id: UUID) -> CampaignOffer | None:
        statement = select(CampaignOffer).where(
            CampaignOffer.business_id == business_id, CampaignOffer.id == offer_id
        )
        return cast(CampaignOffer | None, await self._session.scalar(statement))

    async def list_campaign_offers(
        self, business_id: UUID, *, cursor: Cursor | None, limit: int, active_at: datetime | None
    ) -> list[CampaignOffer]:
        statement: Select[tuple[CampaignOffer]] = select(CampaignOffer).where(
            CampaignOffer.business_id == business_id
        )
        if active_at is not None:
            statement = statement.where(*self.active_campaign_conditions(active_at))
        paged = apply_cursor(
            statement,
            created_at=CampaignOffer.created_at,
            identifier=CampaignOffer.id,
            cursor=cursor,
        ).limit(fetch_size(limit))
        return list((await self._session.scalars(paged)).all())

    @staticmethod
    def active_campaign_conditions(now: datetime) -> tuple[ColumnElement[bool], ...]:
        """The SQL form of `domain.evaluate_campaign_activity`, kept in one place.

        The window is half-open, `[starts_at, ends_at)`, matching the pure rule exactly; an
        integration test asserts the two agree on boundary rows so they cannot drift apart.
        """

        return (
            CampaignOffer.status == CampaignOfferStatus.ACTIVE,
            CampaignOffer.approval_status.in_(
                (CampaignApprovalStatus.NOT_REQUIRED, CampaignApprovalStatus.APPROVED)
            ),
            CampaignOffer.starts_at <= now,
            CampaignOffer.ends_at > now,
        )

    async def count_campaign_offers(self, business_id: UUID) -> int:
        return await self._count(
            select(func.count())
            .select_from(CampaignOffer)
            .where(CampaignOffer.business_id == business_id)
        )

    async def campaign_product_ids(
        self, business_id: UUID, offer_ids: Sequence[UUID]
    ) -> dict[UUID, list[UUID]]:
        if not offer_ids:
            return {}
        statement = (
            select(CampaignOfferProduct.campaign_offer_id, CampaignOfferProduct.product_id)
            .where(
                CampaignOfferProduct.business_id == business_id,
                CampaignOfferProduct.campaign_offer_id.in_(tuple(offer_ids)),
            )
            .order_by(CampaignOfferProduct.created_at, CampaignOfferProduct.id)
        )
        grouped: dict[UUID, list[UUID]] = {offer_id: [] for offer_id in offer_ids}
        for offer_id, product_id in (await self._session.execute(statement)).all():
            grouped.setdefault(offer_id, []).append(product_id)
        return grouped

    async def _count(self, statement: Select[tuple[int]]) -> int:
        return await self._session.scalar(statement) or 0


class MediaAssetReader:
    """The brand module's read-only window onto media, satisfying `MediaAssetPort`.

    Brands needs two facts about media that media does not expose yet: how much usable material
    a tenant has (the health score) and whether a referenced asset really belongs to this tenant
    and finished ingest (brand assets). Both are counted here behind the port so the coupling is
    one named object; when the media module publishes its own inventory read, this class is the
    only thing that is deleted.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def media_inventory(self, business_id: UUID) -> MediaInventory:
        statement = (
            select(
                func.count().filter(MediaAsset.content_type.startswith("image/")),
                func.count().filter(MediaAsset.content_type.startswith("video/")),
            )
            .select_from(MediaAsset)
            .where(MediaAsset.business_id == business_id, *self._usable_conditions())
        )
        row = (await self._session.execute(statement)).one()
        return MediaInventory(photo_count=int(row[0]), video_count=int(row[1]))

    async def usable_asset_ids(
        self, business_id: UUID, asset_ids: Sequence[UUID]
    ) -> frozenset[UUID]:
        if not asset_ids:
            return frozenset()
        statement = select(MediaAsset.id).where(
            MediaAsset.business_id == business_id,
            MediaAsset.id.in_(tuple(asset_ids)),
            *self._usable_conditions(),
        )
        return frozenset((await self._session.scalars(statement)).all())

    @staticmethod
    def _usable_conditions() -> tuple[ColumnElement[bool], ...]:
        """Usable means uploaded and through the ingest gate — not merely a row that exists."""

        return (
            MediaAsset.status == MediaAssetStatus.UPLOADED,
            MediaAsset.ingest_status == IngestStatus.READY_FOR_ANALYSIS,
        )
