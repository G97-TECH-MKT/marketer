"""004 — dispatch_status column on jobs

Revision ID: 004_dispatch_status
Revises: 003_subscription_strategy
Create Date: 2026-04-23

Tracks whether the outbound dispatch (PATCH to router callback OR POST to
agentic-task-dispatcher) succeeded. TEXT for future extensibility.
Values: 'pending', 'ok', 'failed' (initial set; not constrained).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_dispatch_status"
down_revision: Union[str, None] = "003_subscription_strategy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("dispatch_status", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "dispatch_status")
