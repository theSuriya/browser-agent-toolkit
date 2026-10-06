"""Usage metering and plan-quota enforcement.

Metering writes one ``usage_events`` row per billable action (browser-seconds,
agent-tasks, agent-steps, site checks, screenshots). Enforcement reads the current
month's totals and blocks a request only when the plan has a hard cap and it is spent;
plans that allow overage keep working and are billed later.

Everything here takes an :class:`AsyncSession`; callers own the transaction boundary.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.plans import PlanLimits, estimate_overage_cents, get_plan, quota_exceeded
from app.repository import month_start, record_usage, usage_since

# metric -> (included attribute, overage attribute, human unit)
_QUOTA_MAP: dict[str, tuple[str, str, str]] = {
    "browser_seconds": ("included_browser_seconds", "overage_cents_per_browser_sec", "browser-seconds"),
    "agent_tasks": ("included_agent_tasks", "overage_cents_per_agent_task", "agent tasks"),
    "sitecheck": ("included_sitechecks", "", "site checks"),
}


async def meter(
    session: AsyncSession,
    org_id: str,
    metric: str,
    quantity: float,
    key_id: str | None = None,
    job_id: str | None = None,
) -> None:
    """Record a billable event. A zero or negative quantity is ignored."""
    if quantity <= 0:
        return
    await record_usage(session, org_id, metric, quantity, key_id=key_id, job_id=job_id)


async def enforce_quota(session: AsyncSession, org_id: str, plan: PlanLimits, metric: str) -> dict[str, float]:
    """Raise 402 when ``metric`` is over a hard-capped plan quota; else return usage.

    Returns ``{"used": ..., "included": ...}`` so callers can surface remaining budget.
    """
    spec = _QUOTA_MAP.get(metric)
    if spec is None:
        return {"used": 0.0, "included": 0.0}
    included_attr, overage_attr, unit = spec
    included = int(getattr(plan, included_attr))
    overage_rate = float(getattr(plan, overage_attr)) if overage_attr else 0.0
    usage = await usage_since(session, org_id, month_start())
    used = usage.get(metric, 0.0)
    if quota_exceeded(used, included, overage_rate):
        raise AppError(
            402,
            "quota_exceeded",
            f"Monthly {unit} quota for the '{plan.code}' plan is exhausted ({used:g}/{included}). "
            "Upgrade the plan to continue.",
            details=[{"field": metric, "message": f"used {used:g} of {included} {unit}"}],
        )
    return {"used": used, "included": float(included)}


async def usage_summary(session: AsyncSession, org_id: str, plan_code: str | None = None) -> dict:
    """Current-month usage against the plan quota, with overage estimates."""
    plan = get_plan(plan_code)
    usage = await usage_since(session, org_id, month_start())

    def block(metric: str, included: int, overage_rate: float) -> dict:
        used = usage.get(metric, 0.0)
        return {
            "used": round(used, 3),
            "included": included,
            "remaining": None if overage_rate > 0 else round(max(0.0, included - used), 3),
            "overage_cents": estimate_overage_cents(used, included, overage_rate),
        }

    return {
        "plan": plan.code,
        "period_start": month_start().isoformat(),
        "browser_seconds": block("browser_seconds", plan.included_browser_seconds, plan.overage_cents_per_browser_sec),
        "agent_tasks": block("agent_tasks", plan.included_agent_tasks, plan.overage_cents_per_agent_task),
        "sitechecks": block("sitecheck", plan.included_sitechecks, 0.0),
        "agent_steps": round(usage.get("agent_steps", 0.0), 3),
        "screenshots": round(usage.get("screenshots", 0.0), 3),
    }
