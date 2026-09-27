"""Run the research graph and translate its stream into UI events (used by the SSE API
and the answer eval).

Event types: run_started, node_started, token, cost, node_finished, awaiting_approval,
done, error.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, cast

from langgraph.types import Command, Interrupt
from pydantic import BaseModel

from app.agents.graph import ResearchGraph
from app.agents.llm import AgentLLM, OpenAIAgentLLM
from app.agents.nodes import AgentDeps, ReportStore
from app.agents.state import ResearchState
from app.agents.tools import ResearchTools, mcp_tools
from app.config import get_settings
from app.observability.cost import BudgetExceeded, CostTracker

log = logging.getLogger(__name__)

Event = dict[str, Any]  # {"event": type, "data": payload}


class RunStatus(BaseModel):
    thread_id: str
    question: str
    status: str
    pending: dict[str, Any] | None  # the awaiting_approval payload, while paused
    report_id: int | None
    message: str | None
    cost_usd: float


def summarize(node: str, update: dict[str, Any]) -> dict[str, Any]:
    """Small, UI-friendly digest of what a node produced (never the full state)."""
    if node == "supervisor" and "plan" in update:
        plan = update["plan"]
        return {
            "tickers": plan.tickers,
            "queries": plan.filing_queries,
            "financials": plan.need_financials,
            "prices": plan.need_prices,
            "message": update.get("message"),
        }
    if node == "filings":
        return {"passages": len(update.get("evidence", [])), "errors": update.get("errors", [])}
    if node == "market":
        return {
            "financials": sorted(update.get("financials", {})),
            "prices": sorted(update.get("prices", {})),
            "errors": update.get("errors", []),
        }
    if node == "analyst" and "report" in update:
        return {
            "title": update["report"].title,
            "claims": len(update["report"].claims()),
            "revision": update.get("revisions", 0),
        }
    if node == "critic" and "critique" in update:
        verdicts = update["critique"].verdicts
        return {
            "supported": sum(v.supported for v in verdicts),
            "claims": len(verdicts),
            "unsupported": [v.model_dump() for v in verdicts if not v.supported],
        }
    if node == "human_review":
        return {"status": update.get("status"), "report_id": update.get("report_id")}
    return {}


@dataclass
class ResearchService:
    graph: ResearchGraph
    store: ReportStore
    tools_factory: Callable[[], AbstractAsyncContextManager[ResearchTools]] = mcp_tools
    llm_factory: Callable[[CostTracker], AgentLLM] = field(default=OpenAIAgentLLM)

    def _config(self, thread_id: str) -> Any:
        return {"configurable": {"thread_id": thread_id}}

    async def awaiting_approval(self, thread_id: str) -> bool:
        snapshot = await self.graph.aget_state(self._config(thread_id))
        return "human_review" in snapshot.next

    async def run_status(self, thread_id: str) -> RunStatus | None:
        """Where a run stands, from its checkpoint (survives refreshes and restarts)."""
        snapshot = await self.graph.aget_state(self._config(thread_id))
        values = snapshot.values
        if not values:
            return None
        pending = next((i.value for task in snapshot.tasks for i in task.interrupts), None)
        status = values.get("status", "running")
        if pending is not None:
            status = "awaiting_approval"
        return RunStatus(
            thread_id=thread_id,
            question=values.get("question", ""),
            status=status,
            pending=pending,
            report_id=values.get("report_id"),
            message=values.get("message"),
            cost_usd=round(values.get("cost_usd", 0.0), 6),
        )

    async def stream(
        self, thread_id: str, graph_input: ResearchState | Command[Any]
    ) -> AsyncIterator[Event]:
        settings = get_settings()
        tracker = CostTracker(budget_usd=settings.research_budget_usd)
        config = self._config(thread_id)
        yield {"event": "run_started", "data": {"thread_id": thread_id}}
        try:
            async with self.tools_factory() as tools:
                deps = AgentDeps(
                    tools=tools,
                    llm=self.llm_factory(tracker),
                    store=self.store,
                    thread_id=thread_id,
                    max_revisions=settings.max_revisions,
                )
                async for part in self.graph.astream(
                    graph_input, config, context=deps, stream_mode=["updates", "custom"]
                ):
                    # With a list of modes, LangGraph yields (mode, payload) tuples.
                    mode, chunk = cast(tuple[str, dict[str, Any]], part)
                    if mode == "custom":
                        yield {"event": chunk["type"], "data": chunk}
                        continue
                    for node, update in chunk.items():
                        if node == "__interrupt__":
                            interrupt: Interrupt = update[0]
                            yield {
                                "event": "awaiting_approval",
                                "data": {"thread_id": thread_id, **interrupt.value},
                            }
                        else:
                            yield {
                                "event": "node_finished",
                                "data": {"node": node, **summarize(node, update or {})},
                            }

            snapshot = await self.graph.aget_state(config)
            if not snapshot.next:  # finished (not paused at the human review)
                values = snapshot.values
                yield {
                    "event": "done",
                    "data": {
                        "thread_id": thread_id,
                        "status": values.get("status"),
                        "report_id": values.get("report_id"),
                        "message": values.get("message"),
                        "cost_usd": round(values.get("cost_usd", 0.0), 6),
                    },
                }
        except BudgetExceeded as exc:
            yield {"event": "error", "data": {"message": f"Budget stop: {exc}"}}
        except Exception:
            log.exception("research run %s failed", thread_id)
            yield {"event": "error", "data": {"message": "Research run failed; see server logs"}}
