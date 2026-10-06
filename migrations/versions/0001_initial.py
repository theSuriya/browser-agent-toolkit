"""Initial schema: tenancy, keys, plans, subscriptions, metering, jobs, audit.

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BIGINT = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("plan_code", sa.String(length=32), nullable=False, server_default="free"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "org_members",
        sa.Column("org_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('owner','member','readonly')", name="ck_org_members_role"),
    )

    op.create_table(
        "plans",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False, unique=True),
        sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("included_browser_seconds", _BIGINT, nullable=False, server_default="0"),
        sa.Column("included_agent_tasks", _BIGINT, nullable=False, server_default="0"),
        sa.Column("included_sitechecks", _BIGINT, nullable=False, server_default="0"),
        sa.Column("overage_cents_per_browser_sec", sa.Float(), nullable=False, server_default="0"),
        sa.Column("overage_cents_per_agent_task", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("org_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("scopes", sa.Text(), nullable=False, server_default="use"),
        sa.Column("rate_limit", sa.Integer(), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("usage_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_api_keys_org", "api_keys", ["org_id"])
    op.create_index("idx_api_keys_hash", "api_keys", ["key_hash"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("org_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=64), nullable=True),
        sa.Column("stripe_sub_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_subs_org", "subscriptions", ["org_id"])

    op.create_table(
        "usage_events",
        sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_id", sa.String(length=36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "metric IN ('browser_seconds','agent_steps','agent_tasks','sitecheck','screenshots')",
            name="ck_usage_metric",
        ),
    )
    op.create_index("idx_usage_org_time", "usage_events", ["org_id", "created_at"])
    op.create_index("idx_usage_org_metric_time", "usage_events", ["org_id", "metric", "created_at"])
    op.create_index("idx_usage_job", "usage_events", ["job_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("org_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_id", sa.String(length=36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_jobs_org_status", "jobs", ["org_id", "status"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("org_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(length=36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_artifacts_org", "artifacts", ["org_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_audit_org_time", "audit_log", ["org_id", "created_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("artifacts")
    op.drop_table("jobs")
    op.drop_table("usage_events")
    op.drop_table("subscriptions")
    op.drop_table("api_keys")
    op.drop_table("plans")
    op.drop_table("org_members")
    op.drop_table("users")
    op.drop_table("organizations")
