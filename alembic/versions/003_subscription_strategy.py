"""003 — subscription_strategy action + orchestrator_agent column

Revision ID: 003_subscription_strategy
Revises: 002_add_user_profile
Create Date: 2026-04-23

Adds:
  1. Nullable TEXT column `orchestrator_agent` on `jobs` with CHECK constraint
     (values: 'job-router', 'prod-line'). NULL for legacy single-job flows.
  2. New `action_types` row for `subscription_strategy`.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_subscription_strategy"
down_revision: Union[str, None] = "002_add_user_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("orchestrator_agent", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "jobs_orchestrator_agent_check",
        "jobs",
        "orchestrator_agent IN ('job-router', 'prod-line')",
    )

    op.execute(
        """
        INSERT INTO action_types
            (code, surface, mode, prompt_overlay, requires_prior_post, is_enabled, notes)
        VALUES
            ('subscription_strategy', 'other', 'create', 'subscription_strategy', false, true,
             'Multi-job batch: 1 envelope → N PostEnrichments → N callbacks'),
            ('create_prod_line', 'other', 'create', 'create_post', false, true,
             'Default action for subscription_strategy jobs without explicit action_key')
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM action_types WHERE code = 'subscription_strategy'")
    op.drop_constraint("jobs_orchestrator_agent_check", "jobs", type_="check")
    op.drop_column("jobs", "orchestrator_agent")
