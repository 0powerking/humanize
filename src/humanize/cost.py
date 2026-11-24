"""Token-to-yuan accounting and the run budget guard.

Prices are the peak-hour list prices from the provider's pricing page; off-peak is
a configurable factor (official rule: half). Peak windows are Beijing time,
weekdays 09:00-12:00 and 14:00-18:00. Public holidays are not modeled -- on a
holiday the estimate overstates cost, which is the safe direction for a budget.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import CostConfig
from .llm import LLMResult

BEIJING = timezone(timedelta(hours=8))


def now_beijing() -> datetime:
    return datetime.now(BEIJING)


def is_peak(at: datetime) -> bool:
    local = at.astimezone(BEIJING)
    if local.weekday() >= 5:  # Saturday/Sunday
        return False
    return 9 <= local.hour < 12 or 14 <= local.hour < 18


def resolve_peak(cost: CostConfig, at: datetime) -> bool:
    if cost.pricing_mode == "peak":
        return True
    if cost.pricing_mode == "off_peak":
        return False
    return is_peak(at)


def estimate_cost(
    results: Iterable[LLMResult],
    cost: CostConfig,
    at: datetime | None = None,
) -> float:
    """Estimated yuan for these calls. Fully cached results cost nothing."""
    moment = at or now_beijing()
    factor = 1.0 if resolve_peak(cost, moment) else cost.off_peak_factor

    total = 0.0
    for result in results:
        if result.cached:
            continue
        price = cost.models.get(result.model) or cost.models.get("default")
        if price is None:
            continue
        hit = max(0, min(result.prompt_cache_hit_tokens, result.prompt_tokens))
        miss = max(0, result.prompt_tokens - hit)
        total += (
            hit * price.prompt_cache_hit_yuan
            + miss * price.prompt_cache_miss_yuan
            + result.completion_tokens * price.completion_yuan
        ) / 1_000_000 * factor
    return round(total, 6)


@dataclass
class BudgetTracker:
    budget_yuan: float
    warn_ratio: float = 0.8
    spent_yuan: float = 0.0
    warned: bool = False

    def add(self, cost_yuan: float) -> None:
        self.spent_yuan += max(0.0, cost_yuan)
        if not self.warned and self.budget_yuan > 0:
            if self.spent_yuan >= self.budget_yuan * self.warn_ratio:
                self.warned = True

    @property
    def exceeded(self) -> bool:
        return self.budget_yuan > 0 and self.spent_yuan >= self.budget_yuan

    @property
    def remaining_yuan(self) -> float:
        return max(0.0, self.budget_yuan - self.spent_yuan)

