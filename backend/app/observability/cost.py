"""Token usage -> USD, with a hard budget guard.

Every LLM call site records its `response.usage` here; `BudgetExceeded` stops a run before
it can overspend.
"""

from dataclasses import dataclass, field
from typing import Protocol

from app.config import get_settings


class Usage(Protocol):
    input_tokens: int
    output_tokens: int


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CostTracker:
    budget_usd: float
    prices: dict[str, tuple[float, float]] = field(
        default_factory=lambda: get_settings().llm_prices_per_mtok
    )
    input_tokens: int = 0
    output_tokens: int = 0
    spent_usd: float = 0.0
    calls: int = 0

    def record(self, model: str, usage: Usage | None) -> float:
        """Add one call's usage; raise BudgetExceeded once total spend passes the budget."""
        if usage is None:
            raise ValueError(f"No usage returned for {model}; cannot account for cost")
        try:
            price_in, price_out = self.prices[model]
        except KeyError:
            raise ValueError(
                f"No price configured for {model!r}; add it to LLM_PRICES_PER_MTOK"
            ) from None

        cost = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.spent_usd += cost
        self.calls += 1
        if self.spent_usd > self.budget_usd:
            raise BudgetExceeded(
                f"Spent ${self.spent_usd:.4f}, over the ${self.budget_usd:.2f} budget"
            )
        return cost

    def summary(self) -> str:
        return (
            f"{self.calls} calls, {self.input_tokens:,} input + {self.output_tokens:,} output "
            f"tokens, ${self.spent_usd:.4f} of ${self.budget_usd:.2f} budget"
        )
