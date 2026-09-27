"""Langfuse Cloud tracing for research runs; a no-op unless both keys are configured.

One trace per run (and one per review resume), a span per agent node, and a generation per
LLM call with token usage and the cost our CostTracker computed. Observations are created
explicitly rather than as "current" context managers, because the run is an async generator
and OpenTelemetry context does not survive its `yield`s.
"""

import logging
from functools import lru_cache
from typing import Any, Protocol

from app.config import get_settings

log = logging.getLogger(__name__)


class RunTracer(Protocol):
    def node_started(self, node: str) -> None: ...

    def node_finished(self, node: str, summary: dict[str, Any]) -> None: ...

    def llm_call(
        self, node: str, model: str, input_tokens: int, output_tokens: int, cost_usd: float
    ) -> None: ...

    def end(self, status: str, cost_usd: float) -> None: ...


class NoopTracer:
    def node_started(self, node: str) -> None:
        pass

    def node_finished(self, node: str, summary: dict[str, Any]) -> None:
        pass

    def llm_call(
        self, node: str, model: str, input_tokens: int, output_tokens: int, cost_usd: float
    ) -> None:
        pass

    def end(self, status: str, cost_usd: float) -> None:
        pass


@lru_cache
def _client() -> Any:
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    from langfuse import Langfuse

    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
    )


class LangfuseTracer:
    def __init__(self, client: Any, *, name: str, thread_id: str, question: str) -> None:
        self._root = client.start_observation(
            name=name,
            as_type="agent",
            input={"question": question},
            metadata={"thread_id": thread_id},
        )
        self._nodes: dict[str, Any] = {}

    def node_started(self, node: str) -> None:
        self._nodes[node] = self._root.start_observation(name=node, as_type="span")

    def node_finished(self, node: str, summary: dict[str, Any]) -> None:
        span = self._nodes.pop(node, None)
        if span is not None:
            span.update(output=summary)
            span.end()

    def llm_call(
        self, node: str, model: str, input_tokens: int, output_tokens: int, cost_usd: float
    ) -> None:
        parent = self._nodes.get(node, self._root)
        generation = parent.start_observation(
            name=f"{node}.llm",
            as_type="generation",
            model=model,
            usage_details={"input": input_tokens, "output": output_tokens},
            cost_details={"total": cost_usd},
        )
        generation.end()

    def end(self, status: str, cost_usd: float) -> None:
        for span in self._nodes.values():
            span.end()
        self._nodes.clear()
        self._root.update(output={"status": status, "cost_usd": cost_usd})
        self._root.end()


def make_tracer(*, name: str, thread_id: str, question: str) -> RunTracer:
    client = _client()
    if client is None:
        return NoopTracer()
    try:
        return LangfuseTracer(client, name=name, thread_id=thread_id, question=question)
    except Exception:
        log.warning("Langfuse tracing unavailable for this run", exc_info=True)
        return NoopTracer()


def flush() -> None:
    client = _client()
    if client is not None:
        client.flush()
