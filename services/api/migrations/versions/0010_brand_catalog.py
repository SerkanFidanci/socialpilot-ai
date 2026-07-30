"""Add the brand profile, product catalogue, campaign and content-safety tables.

Money columns are `BigInteger` counts of minor units with the ISO-4217 code beside them; there
is deliberately no numeric or floating money column here. Every table carries `business_id` with
a tenant-leading index, because every read of this data is tenant-filtered.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_brand_catalog"
down_revision: str | None = "0009_video_understanding"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TIMESTAMP_DEFAULT = sa.text("timezone('utc', now())")


def _enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
    )


def upgrade() -> None:
    product_status = _enum("product_status", "draft", "active", "archived")
    stock_status = _enum("product_stock_status", "available", "limited", "out_of_stock")
    offer_status = _enum("campaign_offer_status", "draft", "active", "cancelled")
    approval_status = _enum(
        "campaign_approval_status", "not_required", "pending", "approved", "rejected"
    )
    discount_type = _enum("discount_type", "percentage", "fixed_amount")
    asset_role = _enum(
        "brand_asset_role", "logo", "logo_alternate", "style_example", "disliked_example"
    )
    bind = op.get_bind()
    for enum_type in (
        product_status,
        stock_status,
        offer_status,
        approval_status,
        discount_type,
        asset_role,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "brand_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("tone", sa.String(length=200), nullable=False),
        sa.Column("communication_language", sa.String(length=16), nullable=False),
        sa.Column("default_currency", sa.String(length=3), nullable=False),
        sa.Column("font_preference", sa.String(length=120), nullable=True),
        sa.Column("legal_footnote", sa.Text(), nullable=True),
        sa.Column("color_palette", postgresql.JSONB(), nullable=False),
        sa.Column("forbidden_topics", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("business_id", name="uq_brand_profile_business"),
    )

    op.create_table(
        "brand_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", asset_role, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["brand_profile_id"], ["brand_profiles.id"], ondelete="CASCADE"),
        # RESTRICT, not CASCADE: deleting media that a brand still uses as its logo must fail
        # loudly rather than silently emptying the brand identity.
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "brand_profile_id", "media_asset_id", "role", name="uq_brand_asset_role_media"
        ),
    )
    op.create_index("ix_brand_assets_business_role", "brand_assets", ["business_id", "role"])

    op.create_table(
        "target_audiences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("age_min", sa.Integer(), nullable=True),
        sa.Column("age_max", sa.Integer(), nullable=True),
        sa.Column("locations", postgresql.JSONB(), nullable=False),
        sa.Column("interests", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("business_id", "normalized_name", name="uq_target_audience_name"),
    )

    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("normalized_name", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", product_status, nullable=False),
        sa.Column("stock_status", stock_status, nullable=False),
        sa.Column("valid_locations", postgresql.JSONB(), nullable=False),
        sa.Column("landing_page_url", sa.String(length=2048), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("business_id", "normalized_name", name="uq_product_name"),
    )
    op.create_index("ix_products_business_status", "products", ["business_id", "status"])
    # Serves the keyset page order (business_id, created_at DESC, id DESC).
    op.create_index("ix_products_business_created", "products", ["business_id", "created_at", "id"])

    op.create_table(
        "product_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Integer minor units (16500 = 165.00). Never a float: a price is a count.
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.CheckConstraint("price_minor >= 0", name="ck_product_price_non_negative"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_product_price_window",
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_product_prices_product_effective",
        "product_prices",
        ["business_id", "product_id", "effective_from"],
    )
    # Exactly one open price per product: the "current price" question has one answer, enforced
    # by the database rather than by whoever writes the next service method.
    op.create_index(
        "uq_product_prices_open",
        "product_prices",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )

    op.create_table(
        "campaign_offers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("status", offer_status, nullable=False),
        sa.Column("approval_status", approval_status, nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discount_type", discount_type, nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=True),
        sa.Column("discount_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("discount_currency", sa.String(length=3), nullable=True),
        sa.Column("valid_locations", postgresql.JSONB(), nullable=False),
        sa.Column("stock_limit", sa.Integer(), nullable=True),
        sa.Column("coupon_code", sa.String(length=64), nullable=True),
        sa.Column("legal_text", sa.Text(), nullable=True),
        *_timestamps(),
        # A campaign whose window is empty could never be active; reject it at the storage layer.
        sa.CheckConstraint("ends_at > starts_at", name="ck_campaign_offer_window"),
        sa.CheckConstraint(
            "(discount_type = 'percentage' AND discount_percent IS NOT NULL"
            " AND discount_amount_minor IS NULL AND discount_currency IS NULL)"
            " OR (discount_type = 'fixed_amount' AND discount_percent IS NULL"
            " AND discount_amount_minor IS NOT NULL AND discount_currency IS NOT NULL)",
            name="ck_campaign_offer_discount",
        ),
        sa.CheckConstraint(
            "discount_percent IS NULL OR (discount_percent > 0 AND discount_percent <= 100)",
            name="ck_campaign_offer_percent_range",
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_campaign_offers_business_window",
        "campaign_offers",
        ["business_id", "starts_at", "ends_at"],
    )
    op.create_index(
        "ix_campaign_offers_business_created",
        "campaign_offers",
        ["business_id", "created_at", "id"],
    )

    op.create_table(
        "campaign_offer_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["campaign_offer_id"], ["campaign_offers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("campaign_offer_id", "product_id", name="uq_campaign_offer_product"),
    )
    op.create_index(
        "ix_campaign_offer_products_business_offer",
        "campaign_offer_products",
        ["business_id", "campaign_offer_id"],
    )

    for table, constraint in (
        ("approved_claims", "uq_approved_claim"),
        ("forbidden_claims", "uq_forbidden_claim"),
        ("approved_ctas", "uq_approved_cta"),
    ):
        op.create_table(
            table,
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("value", sa.String(length=300), nullable=False),
            sa.Column("lookup_key", sa.String(length=300), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=_TIMESTAMP_DEFAULT,
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("business_id", "lookup_key", name=constraint),
        )


def downgrade() -> None:
    for table in ("approved_ctas", "forbidden_claims", "approved_claims"):
        op.drop_table(table)
    op.drop_index("ix_campaign_offer_products_business_offer", table_name="campaign_offer_products")
    op.drop_table("campaign_offer_products")
    op.drop_index("ix_campaign_offers_business_created", table_name="campaign_offers")
    op.drop_index("ix_campaign_offers_business_window", table_name="campaign_offers")
    op.drop_table("campaign_offers")
    op.execute("DROP INDEX IF EXISTS uq_product_prices_open")
    op.drop_index("ix_product_prices_product_effective", table_name="product_prices")
    op.drop_table("product_prices")
    op.drop_index("ix_products_business_created", table_name="products")
    op.drop_index("ix_products_business_status", table_name="products")
    op.drop_table("products")
    op.drop_table("target_audiences")
    op.drop_index("ix_brand_assets_business_role", table_name="brand_assets")
    op.drop_table("brand_assets")
    op.drop_table("brand_profiles")
    bind = op.get_bind()
    for name in (
        "brand_asset_role",
        "discount_type",
        "campaign_approval_status",
        "campaign_offer_status",
        "product_stock_status",
        "product_status",
    ):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
