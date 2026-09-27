from dataclasses import dataclass

import pytest

from app.observability.cost import BudgetExceeded, CostTracker

PRICES = {"cheap": (1.0, 10.0)}  # USD per 1M tokens


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int


def test_records_cost_and_totals() -> None:
    tracker = CostTracker(budget_usd=1.0, prices=PRICES)
    cost = tracker.record("cheap", _Usage(1_000, 100))

    assert cost == pytest.approx(0.002)  # 1000 * $1/M + 100 * $10/M
    tracker.record("cheap", _Usage(1_000, 100))
    assert tracker.spent_usd == pytest.approx(0.004)
    assert tracker.calls == 2
    assert "2 calls" in tracker.summary()


def test_budget_exceeded_raises() -> None:
    tracker = CostTracker(budget_usd=0.001, prices=PRICES)
    with pytest.raises(BudgetExceeded):
        tracker.record("cheap", _Usage(2_000, 0))


def test_unknown_model_or_missing_usage_refused() -> None:
    tracker = CostTracker(budget_usd=1.0, prices=PRICES)
    with pytest.raises(ValueError, match="No price"):
        tracker.record("mystery-model", _Usage(1, 1))
    with pytest.raises(ValueError, match="No usage"):
        tracker.record("cheap", None)
