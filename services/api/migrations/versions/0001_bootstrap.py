"""Bootstrap Alembic migration without domain tables.

Revision ID: 0001_bootstrap
Revises:
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_bootstrap"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Prove that migration wiring is operational without creating tables."""


def downgrade() -> None:
    """Return to the base revision without changing schema."""
