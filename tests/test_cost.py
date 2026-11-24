from __future__ import annotations

from datetime import datetime

import pytest

from humanize.config import CostConfig, ModelPrice
from humanize.cost import BEIJING, BudgetTracker, estimate_cost, is_peak, resolve_peak
from humanize.llm import LLMResult

FLASH = ModelPrice(
    prompt_cache_hit_yuan=0.04,
    prompt_cache_miss_yuan=2.0,
    completion_yuan=8.0,
)
DEFAULT = ModelPrice(
    prompt_cache_hit_yuan=0.30,
    prompt_cache_miss_yuan=9.0,
    completion_yuan=27.0,
)
COST = CostConfig(
    budget_yuan=50.0,
    off_peak_factor=0.5,
    models={"deepseek-flash": FLASH, "default": DEFAULT},
)


def _result(**overrides) -> LLMResult:
    kwargs = {
        "text": "{}",
        "model": "deepseek-flash",
        "prompt_tokens": 1_000_000,
        "completion_tokens": 1_000_000,
    }
    kwargs.update(overrides)
    return LLMResult(**kwargs)


def test_peak_windows_are_beijing_weekdays() -> None:
    assert is_peak(datetime(2026, 11, 23, 10, 0, tzinfo=BEIJING)) is True  # Monday
    assert is_peak(datetime(2026, 11, 23, 12, 0, tzinfo=BEIJING)) is False
    assert is_peak(datetime(2026, 11, 23, 13, 59, tzinfo=BEIJING)) is False
    assert is_peak(datetime(2026, 11, 23, 14, 0, tzinfo=BEIJING)) is True
    assert is_peak(datetime(2026, 11, 23, 18, 0, tzinfo=BEIJING)) is False
    assert is_peak(datetime(2026, 11, 28, 10, 0, tzinfo=BEIJING)) is False  # Saturday


def test_pricing_mode_override() -> None:
    saturday = datetime(2026, 11, 28, 10, 0, tzinfo=BEIJING)
    peak_mode = COST.model_copy(update={"pricing_mode": "peak"})
    off_mode = COST.model_copy(update={"pricing_mode": "off_peak"})

    assert resolve_peak(peak_mode, saturday) is True
    assert resolve_peak(off_mode, datetime(2026, 11, 23, 10, 0, tzinfo=BEIJING)) is False


def test_peak_price_for_miss_and_output() -> None:
    monday_peak = datetime(2026, 11, 23, 10, 0, tzinfo=BEIJING)
    # 1M miss input (2 yuan) + 1M output (8 yuan)
    assert estimate_cost([_result()], COST, monday_peak) == pytest.approx(10.0)


def test_off_peak_is_half() -> None:
    monday_off = datetime(2026, 11, 23, 13, 0, tzinfo=BEIJING)
    assert estimate_cost([_result()], COST, monday_off) == pytest.approx(5.0)


def test_cache_hit_tokens_use_cheaper_rate() -> None:
    monday_peak = datetime(2026, 11, 23, 10, 0, tzinfo=BEIJING)
    result = _result(
        prompt_tokens=1_000_000,
        completion_tokens=0,
        prompt_cache_hit_tokens=1_000_000,
    )
    assert estimate_cost([result], COST, monday_peak) == pytest.approx(0.04)


def test_cached_result_costs_nothing() -> None:
    monday_peak = datetime(2026, 11, 23, 10, 0, tzinfo=BEIJING)
    result = _result(cached=True)
    assert estimate_cost([result], COST, monday_peak) == 0.0


def test_unknown_model_falls_back_to_default_price() -> None:
    monday_peak = datetime(2026, 11, 23, 10, 0, tzinfo=BEIJING)
    result = _result(model="mystery", prompt_tokens=0, completion_tokens=1_000_000)
    assert estimate_cost([result], COST, monday_peak) == pytest.approx(27.0)


def test_budget_tracker_warns_then_blocks() -> None:
    tracker = BudgetTracker(budget_yuan=10.0, warn_ratio=0.8)

    tracker.add(5.0)
    assert tracker.warned is False
    assert tracker.exceeded is False

    tracker.add(3.5)
    assert tracker.warned is True
    assert tracker.exceeded is False

    tracker.add(2.0)
    assert tracker.exceeded is True
    assert tracker.remaining_yuan == 0.0
