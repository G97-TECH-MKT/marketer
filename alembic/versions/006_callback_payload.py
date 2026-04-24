"""006 — add callback_payload column to jobs

Revision ID: 006_callback_payload
Revises: 005_add_create_prod_line
Create Date: 2026-04-24

Stores the exact payload sent to each external service:
- job-router jobs: the CallbackBody PATCHed to callback_url
- prod-line jobs: the {product_uuid, account_uuid} POSTed to agentic-task-dispatcher

NULL = job created before this migration or no payload recorded yet.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "006_callback_payload"
down_revision: Union[str, None] = "005_add_create_prod_line"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("callback_payload", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "callback_payload")
