"""Harden scene/speech transcript persistence."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008_scene_speech_hardening"
down_revision: str | None = "0007_scene_speech_analysis"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.alter_column(
        "transcripts",
        "full_text",
        existing_type=sa.String(length=20_000),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "transcripts",
        "full_text",
        existing_type=sa.Text(),
        type_=sa.String(length=20_000),
        existing_nullable=False,
    )
