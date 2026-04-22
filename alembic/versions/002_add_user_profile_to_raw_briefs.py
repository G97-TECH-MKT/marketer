"""002 — add user_profile JSONB column to raw_briefs

Revision ID: 002_add_user_profile_to_raw_briefs
Revises: 001_initial_schema
Create Date: 2026-04-22

Adds a nullable JSONB column to store the USP Memory Gateway response
(identity + insights) fetched at the start of each background task.
Nullable because USP may be unavailable and older rows predate this column.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_add_user_profile_to_raw_briefs"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "raw_briefs",
        sa.Column(
            "user_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("raw_briefs", "user_profile")
