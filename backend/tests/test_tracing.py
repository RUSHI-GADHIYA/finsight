from typing import Any

import pytest

from app.config import get_settings
from app.observability import tracing
from app.observability.tracing import LangfuseTracer, NoopTracer, make_tracer


class FakeObservation:
    def __init__(self, log: list[tuple[Any, ...]], name: str) -> None:
        self.log, self.name = log, name

    def start_observation(self, **kwargs: Any) -> "FakeObservation":
        self.log.append(("start", self.name, kwargs["name"], kwargs.get("as_type")))
        if kwargs.get("as_type") == "generation":
            self.log.append(("usage", kwargs["usage_details"], kwargs["cost_details"]))
        return FakeObservation(self.log, kwargs["name"])

    def update(self, **kwargs: Any) -> None:
        self.log.append(("update", self.name))

    def end(self) -> None:
        self.log.append(("end", self.name))


class FakeLangfuse(FakeObservation):
    def __init__(self) -> None:
        super().__init__([], "client")


def test_trace_has_a_span_per_node_and_a_generation_per_llm_call() -> None:
    client = FakeLangfuse()
    tracer = LangfuseTracer(client, name="research", thread_id="t1", question="q")
    tracer.node_started("analyst")
    tracer.llm_call("analyst", "gpt-5", 8000, 2000, 0.03)
    tracer.node_finished("analyst", {"claims": 5})
    tracer.end("awaiting_approval", 0.03)

    assert client.log == [
        ("start", "client", "research", "agent"),
        ("start", "research", "analyst", "span"),
        ("start", "analyst", "analyst.llm", "generation"),
        ("usage", {"input": 8000, "output": 2000}, {"total": 0.03}),
        ("end", "analyst.llm"),
        ("update", "analyst"),
        ("end", "analyst"),
        ("update", "research"),
        ("end", "research"),
    ]


def test_tracing_is_off_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "langfuse_public_key", "")
    tracing._client.cache_clear()
    try:
        assert isinstance(make_tracer(name="research", thread_id="t", question="q"), NoopTracer)
    finally:
        tracing._client.cache_clear()
