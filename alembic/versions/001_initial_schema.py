"""initial schema: users, action_types, raw_briefs, strategies, jobs

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-04-21

Data model agreed in design chat (supersedes docs/PERSISTENCE.md v1):

  users         — mirror of ROUTER's account_uuid; brand metadata JSONB.
  action_types  — catalog (create_post, edit_post, ...); ROUTER declares action_code,
                  we validate/resolve here. prompt_overlay maps to a code file.
  raw_briefs    — durable log of every envelope ROUTER sent (audit + replay +
                  idempotency via UNIQUE(router_task_id)).
  strategies    — per-user brand "alma"; one active per user (partial unique index).
  jobs          — one execution per action; strategy_id pinned at creation.

Naming note: "jobs" here is MARKETER-internal. ROUTER's own job_id is captured as
raw_briefs.router_job_id to avoid confusion.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # gen_random_uuid()
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "brand",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "action_types",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("prompt_overlay", sa.Text(), nullable=False),
        sa.Column(
            "requires_prior_post",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "surface IN ('post', 'web', 'other')", name="action_types_surface_check"
        ),
        sa.CheckConstraint(
            "mode IN ('create', 'edit', 'other')", name="action_types_mode_check"
        ),
    )

    op.execute(
        """
        INSERT INTO action_types
            (code, surface, mode, prompt_overlay, requires_prior_post, is_enabled, notes)
        VALUES
            ('create_post', 'post', 'create', 'create_post', false, true,
             'MVP - posts-only surface enabled'),
            ('edit_post',   'post', 'edit',   'edit_post',   true,  true,
             'MVP; requires prior_post in envelope'),
            ('create_web',  'web',  'create', 'create_web',  false, false,
             'Overlay exists; gated off until ATLAS integration'),
            ('edit_web',    'web',  'edit',   'edit_web',    false, false,
             'Overlay exists; gated off')
        """
    )

    op.create_table(
        "raw_briefs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("router_task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("router_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("router_correlation_id", sa.Text(), nullable=True),
        sa.Column("action_code", sa.Text(), nullable=False),
        sa.Column("envelope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'received'"),
        ),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="raw_briefs_user_id_fkey"),
        sa.ForeignKeyConstraint(
            ["action_code"], ["action_types.code"], name="raw_briefs_action_code_fkey"
        ),
        sa.UniqueConstraint("router_task_id", name="raw_briefs_router_task_id_key"),
        sa.CheckConstraint(
            "status IN ('received', 'processing', 'done', 'failed')",
            name="raw_briefs_status_check",
        ),
    )
    op.create_index(
        "idx_raw_briefs_user_time",
        "raw_briefs",
        ["user_id", sa.text("received_at DESC")],
    )
    op.create_index(
        "idx_raw_briefs_status", "raw_briefs", ["status", "received_at"]
    )

    op.create_table(
        "strategies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "brand_intelligence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="strategies_user_id_fkey"
        ),
        sa.UniqueConstraint(
            "user_id", "version", name="strategies_user_version_key"
        ),
    )
    # One active strategy per user (partial unique index — Postgres-only).
    op.execute(
        "CREATE UNIQUE INDEX strategies_one_active_per_user "
        "ON strategies (user_id) WHERE is_active"
    )

    op.create_table(
        "jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_brief_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_code", sa.Text(), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="jobs_user_id_fkey"),
        sa.ForeignKeyConstraint(
            ["raw_brief_id"], ["raw_briefs.id"], name="jobs_raw_brief_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"], ["strategies.id"], name="jobs_strategy_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["action_code"], ["action_types.code"], name="jobs_action_code_fkey"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')",
            name="jobs_status_check",
        ),
    )
    op.create_index(
        "idx_jobs_user_time", "jobs", ["user_id", sa.text("created_at DESC")]
    )
    op.create_index("idx_jobs_status", "jobs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_jobs_status", table_name="jobs")
    op.drop_index("idx_jobs_user_time", table_name="jobs")
    op.drop_table("jobs")

    op.execute("DROP INDEX IF EXISTS strategies_one_active_per_user")
    op.drop_table("strategies")

    op.drop_index("idx_raw_briefs_status", table_name="raw_briefs")
    op.drop_index("idx_raw_briefs_user_time", table_name="raw_briefs")
    op.drop_table("raw_briefs")

    op.drop_table("action_types")
    op.drop_table("users")
