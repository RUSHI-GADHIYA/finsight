"""Run the research graph and translate its stream into UI events (used by the SSE API
and the answer eval).

Event types: run_started, node_started, token, cost, guardrail, node_finished,
awaiting_approval, cache_hit, done, error.

Also the one place that writes operational records: run cost and latency, the audit log,
Langfuse traces, and the semantic cache of approved reports.
"""

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, cast

from langgraph.types import Command, Interrupt
from pydantic import BaseModel

from app.agents.cache import SemanticCache
from app.agents.graph import ResearchGraph
from app.agents.llm import AgentLLM, OpenAIAgentLLM
from app.agents.nodes import AgentDeps, ReportStore
from app.agents.state import ResearchState
from app.agents.tools import ResearchTools, mcp_tools
from app.config import get_settings
from app.guardrails.injection import InjectionGuard
from app.guardrails.pii import redact
from app.observability.cost import BudgetExceeded, CostTracker
from app.observability.ops import OpsLog
from app.observability.tracing import RunTracer, make_tracer

log = logging.getLogger(__name__)

Event = dict[str, Any]  # {"event": type, "data": payload}


class Screened(BaseModel):
    question: str  # with any personal data redacted
    blocked: bool
    redacted: list[str]


class NullOps:
    async def event(self, thread_id: str | None, event: str, detail: dict[str, Any]) -> None:
        pass

    async def run_started(self, thread_id: str, question: str, *, cache_hit: bool = False) -> None:
        pass

    async def run_updated(
        self,
        thread_id: str,
        *,
        status: str,
        cost_usd: float | None = None,
        latency_ms: int | None = None,
        node_timings: dict[str, int] | None = None,
    ) -> None:
        pass


def _safe(fn: Callable[..., None], *args: Any) -> None:
    """Tracing must never break a run."""
    try:
        fn(*args)
    except Exception:
        log.warning("tracing call failed", exc_info=True)


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
    ops: OpsLog = field(default_factory=NullOps)
    cache: SemanticCache | None = None
    guard: InjectionGuard | None = None
    tracer_factory: Callable[..., RunTracer] = make_tracer

    def _config(self, thread_id: str) -> Any:
        return {"configurable": {"thread_id": thread_id}}

    async def screen(self, question: str) -> Screened:
        """Input guardrails: redact personal data, then block prompt-injection attempts."""
        redaction = redact(question)
        if redaction.kinds:
            await self.ops.event(None, "pii_redacted", {"kinds": redaction.kinds})
        if self.guard is not None:
            score = (await self.guard.flagged([redaction.text]))[0]
            if score is not None:
                await self.ops.event(
                    None,
                    "input_blocked",
                    {"question": redaction.text[:500], "score": round(score, 4)},
                )
                return Screened(question=redaction.text, blocked=True, redacted=redaction.kinds)
        return Screened(question=redaction.text, blocked=False, redacted=redaction.kinds)

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

    async def start(
        self, thread_id: str, question: str, *, fresh: bool = False
    ) -> AsyncIterator[Event]:
        """A new run. A question close enough to one a human already signed off is answered
        from the semantic cache instead, at no LLM cost (unless `fresh`)."""
        if self.cache is not None and not fresh:
            hit = await self.cache.lookup(question)
            if hit is not None:
                await self.ops.run_started(thread_id, question, cache_hit=True)
                await self.ops.run_updated(thread_id, status="cached", cost_usd=0.0, latency_ms=0)
                await self.ops.event(thread_id, "cache_hit", hit.model_dump(mode="json"))
                yield {"event": "run_started", "data": {"thread_id": thread_id}}
                yield {"event": "cache_hit", "data": hit.model_dump(mode="json")}
                yield {
                    "event": "done",
                    "data": {
                        "thread_id": thread_id,
                        "status": "cached",
                        "report_id": hit.report_id,
                        "message": None,
                        "cost_usd": 0.0,
                    },
                }
                return
        await self.ops.run_started(thread_id, question)
        async for event in self._run(thread_id, {"question": question}, question, "research"):
            yield event

    async def resume(self, thread_id: str, decision: dict[str, Any]) -> AsyncIterator[Event]:
        snapshot = await self.graph.aget_state(self._config(thread_id))
        question = snapshot.values.get("question", "")
        async for event in self._run(thread_id, Command(resume=decision), question, "review"):
            yield event

    async def _run(
        self,
        thread_id: str,
        graph_input: ResearchState | Command[Any],
        question: str,
        trace_name: str,
    ) -> AsyncIterator[Event]:
        settings = get_settings()
        tracker = CostTracker(budget_usd=settings.research_budget_usd)
        config = self._config(thread_id)
        tracer = self.tracer_factory(name=trace_name, thread_id=thread_id, question=question)
        started = time.monotonic()
        node_started: dict[str, float] = {}
        timings: dict[str, int] = {}
        status = "error"
        cost = 0.0
        yield {"event": "run_started", "data": {"thread_id": thread_id}}
        try:
            async with self.tools_factory() as tools:
                deps = AgentDeps(
                    tools=tools,
                    llm=self.llm_factory(tracker),
                    store=self.store,
                    thread_id=thread_id,
                    max_revisions=settings.max_revisions,
                    guard=self.guard,
                )
                async for part in self.graph.astream(
                    graph_input, config, context=deps, stream_mode=["updates", "custom"]
                ):
                    # With a list of modes, LangGraph yields (mode, payload) tuples.
                    mode, chunk = cast(tuple[str, dict[str, Any]], part)
                    if mode == "custom":
                        await self._on_custom(thread_id, chunk, tracer, node_started)
                        yield {"event": chunk["type"], "data": chunk}
                        continue
                    for node, update in chunk.items():
                        if node == "__interrupt__":
                            interrupt: Interrupt = update[0]
                            yield {
                                "event": "awaiting_approval",
                                "data": {"thread_id": thread_id, **interrupt.value},
                            }
                            continue
                        summary = summarize(node, update or {})
                        if node in node_started:
                            elapsed = time.monotonic() - node_started.pop(node)
                            timings[node] = timings.get(node, 0) + int(elapsed * 1000)
                        _safe(tracer.node_finished, node, summary)
                        yield {"event": "node_finished", "data": {"node": node, **summary}}

            snapshot = await self.graph.aget_state(config)
            values = snapshot.values
            cost = round(values.get("cost_usd", 0.0), 6)
            status = "awaiting_approval" if snapshot.next else values.get("status", "done")
            if not snapshot.next:  # finished (not paused at the human review)
                yield {
                    "event": "done",
                    "data": {
                        "thread_id": thread_id,
                        "status": status,
                        "report_id": values.get("report_id"),
                        "message": values.get("message"),
                        "cost_usd": cost,
                    },
                }
            if isinstance(graph_input, Command):
                await self._after_review(thread_id, graph_input, values)
        except BudgetExceeded as exc:
            status = "budget_stop"
            cost = round(tracker.spent_usd, 6)
            await self.ops.event(thread_id, "budget_stop", {"message": str(exc)})
            yield {"event": "error", "data": {"message": f"Budget stop: {exc}"}}
        except Exception as exc:
            log.exception("research run %s failed", thread_id)
            await self.ops.event(thread_id, "run_error", {"error": type(exc).__name__})
            yield {"event": "error", "data": {"message": "Research run failed; see server logs"}}
        finally:
            _safe(tracer.end, status, cost)
            if isinstance(graph_input, Command):  # a review: cost and latency are the run's
                await self.ops.run_updated(thread_id, status=status)
            else:
                await self.ops.run_updated(
                    thread_id,
                    status=status,
                    cost_usd=cost,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    node_timings=timings,
                )

    async def _on_custom(
        self,
        thread_id: str,
        chunk: dict[str, Any],
        tracer: RunTracer,
        node_started: dict[str, float],
    ) -> None:
        kind = chunk.get("type")
        if kind == "node_started":
            node_started[chunk["node"]] = time.monotonic()
            _safe(tracer.node_started, chunk["node"])
        elif kind == "cost":
            _safe(
                tracer.llm_call,
                chunk["node"],
                chunk.get("model", ""),
                int(chunk.get("input_tokens", 0)),
                int(chunk.get("output_tokens", 0)),
                float(chunk.get("call_usd", 0.0)),
            )
        elif kind == "guardrail":
            detail = {k: v for k, v in chunk.items() if k not in ("type", "kind")}
            await self.ops.event(thread_id, chunk["kind"], detail)

    async def _after_review(
        self, thread_id: str, decision: Command[Any], values: dict[str, Any]
    ) -> None:
        resume = decision.resume if isinstance(decision.resume, dict) else {}
        action = resume.get("action", "")
        event = {
            "approve": "report_approved",
            "edit": "report_edited",
            "reject": "report_rejected",
        }.get(action)
        report_id = values.get("report_id")
        if event:
            await self.ops.event(thread_id, event, {"report_id": report_id})
        if self.cache is not None and report_id is not None and action in ("approve", "edit"):
            await self.cache.add(values.get("question", ""), report_id)
