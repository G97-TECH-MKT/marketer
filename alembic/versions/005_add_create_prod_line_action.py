"""005 — add create_prod_line action_type (backfill)

Revision ID: 005_add_create_prod_line
Revises: 004_dispatch_status
Create Date: 2026-04-23

Migration 003 was edited after initial deploy to include create_prod_line,
but databases that already ran 003 are missing this row. This migration
backfills it safely with ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "005_add_create_prod_line"
down_revision: Union[str, None] = "004_dispatch_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO action_types
            (code, surface, mode, prompt_overlay, requires_prior_post, is_enabled, notes)
        VALUES
            ('create_prod_line', 'other', 'create', 'create_post', false, true,
             'Default action for subscription_strategy jobs without explicit action_key')
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM action_types WHERE code = 'create_prod_line'")
