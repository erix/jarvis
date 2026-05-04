"""Track API usage and enforce cost ceiling."""
import logging

logger = logging.getLogger(__name__)

# OpenRouter pricing for anthropic/claude-sonnet-4-6 (per token)
_PRICE_INPUT = 3.00 / 1_000_000
_PRICE_OUTPUT = 15.00 / 1_000_000


class CostTracker:
    def __init__(self, ceiling: float = 10.0):
        self.ceiling = ceiling
        self.total_cost = 0.0
        self.api_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self._ceiling_hit = False

    def record(self, usage) -> None:
        input_t = getattr(usage, "prompt_tokens", 0) or getattr(usage, "input_tokens", 0) or 0
        output_t = getattr(usage, "completion_tokens", 0) or getattr(usage, "output_tokens", 0) or 0

        cost = input_t * _PRICE_INPUT + output_t * _PRICE_OUTPUT

        self.total_cost += cost
        self.api_calls += 1
        self.input_tokens += input_t
        self.output_tokens += output_t

        if self.total_cost >= self.ceiling and not self._ceiling_hit:
            self._ceiling_hit = True
            logger.warning(
                "Cost ceiling $%.2f hit after %d API calls ($%.4f spent) — skipping remaining tickers",
                self.ceiling, self.api_calls, self.total_cost,
            )

    @property
    def ceiling_hit(self) -> bool:
        return self._ceiling_hit

    def summary(self) -> dict:
        return {
            "api_calls": self.api_calls,
            "total_cost": round(self.total_cost, 4),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
