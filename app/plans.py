"""Plan catalog and the limits each plan enforces.

The catalog is the single source of truth for what a tenant may do: rate limit,
session caps, agent step budget, and included metered quotas. It is seeded into the
``plans`` table on startup (for billing amounts) and read directly at request time
for enforcement, so a plan change is one edit here plus a re-seed.

Billing unit is browser-seconds and agent-tasks, never API calls: an API call is a
routing event that does not map to Chromium memory or CPU.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PLAN = "free"

_MINUTE = 60


@dataclass(frozen=True)
class PlanLimits:
    code: str
    price_cents: int
    rate_limit_per_min: int
    max_sessions_per_key: int
    max_agent_steps: int
    included_browser_seconds: int
    included_agent_tasks: int
    included_sitechecks: int
    # Cents per unit past the included quota. 0 means a hard cap (no overage).
    overage_cents_per_browser_sec: float
    overage_cents_per_agent_task: float

    @property
    def overage_enabled(self) -> bool:
        return self.overage_cents_per_browser_sec > 0 or self.overage_cents_per_agent_task > 0


PLANS: dict[str, PlanLimits] = {
    "free": PlanLimits(
        code="free",
        price_cents=0,
        rate_limit_per_min=30,
        max_sessions_per_key=1,
        max_agent_steps=10,
        included_browser_seconds=30 * _MINUTE,      # 30 browser-minutes
        included_agent_tasks=5,
        included_sitechecks=20,
        overage_cents_per_browser_sec=0.0,
        overage_cents_per_agent_task=0.0,
    ),
    "developer": PlanLimits(
        code="developer",
        price_cents=1900,
        rate_limit_per_min=120,
        max_sessions_per_key=4,
        max_agent_steps=25,
        included_browser_seconds=300 * _MINUTE,     # 5 browser-hours
        included_agent_tasks=50,
        included_sitechecks=500,
        overage_cents_per_browser_sec=0.10 / _MINUTE,    # $0.10 per browser-minute
        overage_cents_per_agent_task=2.0,                # $0.02 per task
    ),
    "team": PlanLimits(
        code="team",
        price_cents=9900,
        rate_limit_per_min=600,
        max_sessions_per_key=20,
        max_agent_steps=50,
        included_browser_seconds=3000 * _MINUTE,    # 50 browser-hours
        included_agent_tasks=500,
        included_sitechecks=5000,
        overage_cents_per_browser_sec=0.08 / _MINUTE,    # $0.08 per browser-minute
        overage_cents_per_agent_task=1.5,
    ),
    "enterprise": PlanLimits(
        code="enterprise",
        price_cents=0,                              # negotiated
        rate_limit_per_min=6000,
        max_sessions_per_key=100,
        max_agent_steps=100,
        included_browser_seconds=100_000_000,
        included_agent_tasks=10_000_000,
        included_sitechecks=10_000_000,
        overage_cents_per_browser_sec=0.05 / _MINUTE,
        overage_cents_per_agent_task=1.0,
    ),
}


def get_plan(code: str | None) -> PlanLimits:
    """Return the plan for ``code``, falling back to the default plan."""
    return PLANS.get(code or DEFAULT_PLAN, PLANS[DEFAULT_PLAN])


def quota_exceeded(used: float, included: int, overage_rate: float) -> bool:
    """True only when the included quota is spent and the plan allows no overage.

    Plans with overage keep working and get billed; plans without it are hard-capped.
    """
    return overage_rate <= 0 and used >= included


def estimate_overage_cents(used: float, included: int, overage_rate: float) -> float:
    if overage_rate <= 0 or used <= included:
        return 0.0
    return round((used - included) * overage_rate, 4)
