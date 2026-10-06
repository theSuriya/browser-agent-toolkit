"""Phase 2: job lifecycle columns (attempts, worker_id, started_at).

Revision ID: 0002_phase2_jobs
Revises: 0001_initial
Create Date: 2026-10-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_phase2_jobs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("worker_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(
            "ck_jobs_status", "status IN ('queued','running','succeeded','failed')"
        )
        batch.create_index("idx_jobs_status_created", ["status", "created_at"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("idx_jobs_status_created")
        batch.drop_constraint("ck_jobs_status", type_="check")
        batch.drop_column("started_at")
        batch.drop_column("worker_id")
        batch.drop_column("attempts")
