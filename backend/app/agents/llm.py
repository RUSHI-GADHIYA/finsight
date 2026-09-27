"""Structured LLM calls for the agents: OpenAI Responses API + Pydantic schema + cost guard.

Nodes depend on the `AgentLLM` protocol, so tests swap in canned outputs with no API key.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from app.config import get_settings
from app.llm import get_openai_client
from app.observability.cost import CostTracker

Tier = Literal["fast", "smart"]
StreamWriter = Callable[[Any], None]

# Reasoning tokens are billed as output; keep effort low; the heavy lifting is retrieval.
_REASONING: dict[Tier, Literal["minimal", "low"]] = {"fast": "minimal", "smart": "low"}


class LLMError(RuntimeError):
    pass


class AgentLLM(Protocol):
    async def parse[T: BaseModel](
        self,
        *,
        node: str,
        tier: Tier,
        instructions: str,
        input: str,
        schema: type[T],
        max_output_tokens: int,
        writer: StreamWriter,
    ) -> tuple[T, float]:
        """Return the parsed output and this call's cost in USD."""
        ...


@dataclass
class OpenAIAgentLLM:
    tracker: CostTracker

    async def parse[T: BaseModel](
        self,
        *,
        node: str,
        tier: Tier,
        instructions: str,
        input: str,
        schema: type[T],
        max_output_tokens: int,
        writer: StreamWriter,
    ) -> tuple[T, float]:
        settings = get_settings()
        model = settings.llm_smart_model if tier == "smart" else settings.llm_fast_model
        # `instructions` is the stable prefix (cache-friendly); `input` carries the per-run data.
        async with get_openai_client().responses.stream(
            model=model,
            instructions=instructions,
            input=input,
            text_format=schema,
            reasoning={"effort": _REASONING[tier]},
            max_output_tokens=max_output_tokens,
        ) as stream:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    writer({"type": "token", "node": node, "delta": event.delta})
            response = await stream.get_final_response()

        cost = self.tracker.record(model, response.usage)  # raises BudgetExceeded
        writer(
            {
                "type": "cost",
                "node": node,
                "model": model,
                "call_usd": round(cost, 6),
                "spent_usd": round(self.tracker.spent_usd, 6),
            }
        )
        if response.output_parsed is None:
            reason = response.incomplete_details or response.status
            raise LLMError(f"{node}: no structured output from {model} ({reason})")
        return response.output_parsed, cost
