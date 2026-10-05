"""Metering records usage and plan quotas block only hard-capped plans (no browser)."""

import httpx
import pytest

from app import metering, repository
from app.db import init_models, make_engine, make_sessionmaker
from app.errors import AppError
from app.plans import get_plan


@pytest.fixture
async def sessionmaker(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/metering.db")
    await init_models(engine)
    factory = make_sessionmaker(engine)
    yield factory
    await engine.dispose()


async def test_meter_records_and_summarizes(sessionmaker):
    async with sessionmaker() as session:
        org = await repository.create_organization(session, "Meter Co", "free")
        await session.commit()
        await metering.meter(session, org.id, "browser_seconds", 120)
        await metering.meter(session, org.id, "agent_tasks", 2)
        await metering.meter(session, org.id, "agent_steps", 7)

    async with sessionmaker() as session:
        summary = await metering.usage_summary(session, org.id, "free")
    assert summary["browser_seconds"]["used"] == 120
    assert summary["agent_tasks"]["used"] == 2
    assert summary["agent_steps"] == 7


async def test_zero_quantity_is_ignored(sessionmaker):
    async with sessionmaker() as session:
        org = await repository.create_organization(session, "Zero Co", "free")
        await session.commit()
        await metering.meter(session, org.id, "browser_seconds", 0)
    async with sessionmaker() as session:
        summary = await metering.usage_summary(session, org.id, "free")
    assert summary["browser_seconds"]["used"] == 0


async def test_hard_capped_plan_blocks_when_quota_spent(sessionmaker):
    plan = get_plan("free")  # overage disabled -> hard cap
    async with sessionmaker() as session:
        org = await repository.create_organization(session, "Capped Co", "free")
        await session.commit()
        await metering.meter(session, org.id, "browser_seconds", plan.included_browser_seconds)
    async with sessionmaker() as session:
        with pytest.raises(AppError) as exc:
            await metering.enforce_quota(session, org.id, plan, "browser_seconds")
    assert exc.value.status_code == 402
    assert exc.value.code == "quota_exceeded"


async def test_overage_plan_is_not_blocked_and_reports_overage(sessionmaker):
    plan = get_plan("developer")  # overage enabled
    async with sessionmaker() as session:
        org = await repository.create_organization(session, "Overage Co", "developer")
        await session.commit()
        await metering.meter(session, org.id, "browser_seconds", plan.included_browser_seconds + 600)
    async with sessionmaker() as session:
        result = await metering.enforce_quota(session, org.id, plan, "browser_seconds")
        assert result["used"] > result["included"]
        summary = await metering.usage_summary(session, org.id, "developer")
    assert summary["browser_seconds"]["overage_cents"] > 0
    assert summary["browser_seconds"]["remaining"] is None  # overage plans have no hard remaining


async def _prepare(app):
    from app import repository

    await init_models(app.state.engine)
    async with app.state.sessionmaker() as session:
        await repository.seed_plans(session)


@pytest.fixture
def authed_app(tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.db"))
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    application = create_app()
    yield application
    get_settings.cache_clear()


async def test_http_request_is_blocked_when_quota_is_spent(authed_app):
    await _prepare(authed_app)
    transport = httpx.ASGITransport(app=authed_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        signup = await client.post(
            "/accounts/signup", json={"email": "q@acme.test", "org_name": "Quota Co"}
        )
        body = signup.json()
        headers = {"Authorization": f"Bearer {body['key']}"}

        # Spend the entire free browser-seconds quota, then a session request is 402.
        async with authed_app.state.sessionmaker() as session:
            await metering.meter(session, body["org_id"], "browser_seconds", 1800)

        blocked = await client.post("/sessions", headers=headers)
        assert blocked.status_code == 402
        assert blocked.json()["error"]["code"] == "quota_exceeded"

        # The usage endpoint reflects the spent quota.
        usage = (await client.get("/usage", headers=headers)).json()
        assert usage["browser_seconds"]["used"] == 1800
