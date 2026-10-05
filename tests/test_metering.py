"""Usage metering and plan-quota enforcement against a real (temporary) database.

No browser is needed: these exercise the persistence and enforcement layer directly.
"""

import pytest

from app import metering, repository
from app.db import init_models, make_engine, make_sessionmaker
from app.errors import AppError
from app.plans import get_plan, quota_exceeded, estimate_overage_cents


@pytest.fixture
async def db(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'metering.db'}")
    await init_models(engine)
    factory = make_sessionmaker(engine)
    async with factory() as session:
        await repository.seed_plans(session)
    yield factory
    await engine.dispose()


async def test_usage_since_sums_metrics(db):
    async with db() as session:
        org = await repository.create_organization(session, "Acme", "free")
        await repository.record_usage(session, org.id, "browser_seconds", 30)
        await repository.record_usage(session, org.id, "browser_seconds", 12.5)
        await repository.record_usage(session, org.id, "agent_tasks", 1)
        totals = await repository.usage_since(session, org.id, repository.month_start())
    assert totals["browser_seconds"] == 42.5
    assert totals["agent_tasks"] == 1


async def test_hard_capped_plan_raises_402(db):
    plan = get_plan("free")  # overage disabled -> hard cap
    async with db() as session:
        org = await repository.create_organization(session, "Acme", "free")
        await repository.record_usage(session, org.id, "browser_seconds", plan.included_browser_seconds + 1)
        with pytest.raises(AppError) as exc:
            await metering.enforce_quota(session, org.id, plan, "browser_seconds")
    assert exc.value.status_code == 402
    assert exc.value.code == "quota_exceeded"


async def test_overage_plan_does_not_block(db):
    plan = get_plan("developer")  # overage enabled -> keeps working
    async with db() as session:
        org = await repository.create_organization(session, "Acme", "developer")
        await repository.record_usage(session, org.id, "browser_seconds", plan.included_browser_seconds + 60)
        result = await metering.enforce_quota(session, org.id, plan, "browser_seconds")
    assert result["included"] == plan.included_browser_seconds
    assert result["used"] == plan.included_browser_seconds + 60


async def test_usage_summary_estimates_overage(db):
    plan = get_plan("developer")
    async with db() as session:
        org = await repository.create_organization(session, "Acme", "developer")
        await repository.record_usage(session, org.id, "browser_seconds", plan.included_browser_seconds + 60)
        summary = await metering.usage_summary(session, org.id, "developer")
    # 60 seconds over at $0.10/browser-minute = 10 cents.
    assert summary["browser_seconds"]["overage_cents"] == pytest.approx(10.0, abs=0.01)


def test_quota_helpers():
    assert quota_exceeded(5, 5, 0.0) is True          # hard cap, exhausted
    assert quota_exceeded(4, 5, 0.0) is False
    assert quota_exceeded(10, 5, 0.1) is False        # overage plan never blocks
    assert estimate_overage_cents(10, 4, 2.0) == 12.0
    assert estimate_overage_cents(3, 4, 2.0) == 0.0
