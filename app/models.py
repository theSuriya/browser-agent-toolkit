"""SQLAlchemy 2.0 models: tenancy, billing, metering, jobs and audit.

Portable across SQLite (dev/tests) and PostgreSQL (production): identifiers are
strings, JSON columns use the generic ``JSON`` type, and the surrogate integer keys
fall back to SQLite's ``INTEGER`` so autoincrement works there. Money is stored in
integer cents; timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


def big_int_pk() -> Mapped[int]:
    """Autoincrementing integer primary key that works on SQLite and Postgres."""
    return mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)


# -- Identity and tenancy ---------------------------------------------------
class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class OrgMember(Base):
    __tablename__ = "org_members"
    __table_args__ = (CheckConstraint("role IN ('owner','member','readonly')", name="ck_org_members_role"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Comma-separated scopes ("admin", "use"); kept as text for SQLite/Postgres parity.
    scopes: Mapped[str] = mapped_column(Text, nullable=False, default="use")
    rate_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Persisted usage fields: previously in-memory only (app/auth.py) and lost on restart.
    usage_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("idx_api_keys_org", "org_id"),
        Index("idx_api_keys_hash", "key_hash"),
    )


# -- Plans and subscriptions ------------------------------------------------
class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    included_browser_seconds: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    included_agent_tasks: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    included_sitechecks: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    overage_cents_per_browser_sec: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    overage_cents_per_agent_task: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_sub_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (Index("idx_subs_org", "org_id"),)


# -- Metering ---------------------------------------------------------------
class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint(
            "metric IN ('browser_seconds','agent_steps','agent_tasks','sitecheck','screenshots')",
            name="ck_usage_metric",
        ),
        Index("idx_usage_org_time", "org_id", "created_at"),
        Index("idx_usage_org_metric_time", "org_id", "metric", "created_at"),
        Index("idx_usage_job", "job_id"),
    )

    id: Mapped[int] = big_int_pk()
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    key_id: Mapped[str | None] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


# -- Jobs and artifacts -----------------------------------------------------
class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("idx_jobs_org_status", "org_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    key_id: Mapped[str | None] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    input: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (Index("idx_artifacts_org", "org_id"),)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("idx_audit_org_time", "org_id", "created_at"),)

    id: Mapped[int] = big_int_pk()
    org_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
