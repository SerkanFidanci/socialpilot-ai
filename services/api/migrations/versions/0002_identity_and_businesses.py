"""Create identity, business, and membership foundations.

Revision ID: 0002_identity_and_businesses
Revises: 0001_bootstrap
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_identity_and_businesses"
down_revision: str | None = "0001_bootstrap"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

user_status = postgresql.ENUM(
    "active", "suspended", "deleted", name="user_status", create_type=False
)
business_status = postgresql.ENUM(
    "active", "suspended", "archived", name="business_status", create_type=False
)
business_role = postgresql.ENUM(
    "owner", "admin", "editor", "viewer", name="business_role", create_type=False
)
membership_status = postgresql.ENUM(
    "invited", "active", "suspended", "removed", name="membership_status", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    user_status.create(bind, checkfirst=True)
    business_status.create(bind, checkfirst=True)
    business_role.create(bind, checkfirst=True)
    membership_status.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=True),
        sa.Column("status", user_status, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_normalized"),
    )
    op.create_index("uq_users_normalized_email", "users", [sa.text("lower(email)")], unique=True)
    op.create_table(
        "external_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_subject", sa.String(length=255), nullable=False),
        sa.Column("email_at_provider", sa.String(length=320), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_external_identity"),
    )
    op.create_index("ix_external_identities_user_id", "external_identities", ["user_id"])
    op.create_table(
        "businesses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=180), nullable=False),
        sa.Column("status", business_status, nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_businesses_slug", "businesses", ["slug"])
    op.create_index("ix_businesses_created_by_user_id", "businesses", ["created_by_user_id"])
    op.create_table(
        "business_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", business_role, nullable=False),
        sa.Column("status", membership_status, nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "user_id", name="uq_business_member"),
    )
    op.create_index("ix_business_members_user_status", "business_members", ["user_id", "status"])
    op.create_index(
        "ix_business_members_business_status_role",
        "business_members",
        ["business_id", "status", "role"],
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_business_members_business_status_role")
    op.execute("DROP INDEX IF EXISTS ix_business_members_business_status")
    op.execute("DROP INDEX IF EXISTS ix_business_members_user_status")
    op.drop_table("business_members")
    op.execute("DROP INDEX IF EXISTS ix_businesses_created_by_user_id")
    op.execute("DROP INDEX IF EXISTS ix_businesses_slug")
    op.drop_table("businesses")
    op.execute("DROP INDEX IF EXISTS ix_external_identities_user_id")
    op.drop_table("external_identities")
    op.execute("DROP INDEX IF EXISTS uq_users_normalized_email")
    op.execute("DROP INDEX IF EXISTS ix_users_email")
    op.drop_table("users")
    bind = op.get_bind()
    membership_status.drop(bind, checkfirst=True)
    business_role.drop(bind, checkfirst=True)
    business_status.drop(bind, checkfirst=True)
    user_status.drop(bind, checkfirst=True)
