"""Wire the research graph.

START -> supervisor -> filings --+--> analyst -> critic --> human_review -> END
                  \\-> market ---/      ^           |
                                       +-----------+  (unsupported claims, max N revisions)
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents import nodes
from app.agents.nodes import AgentDeps
from app.agents.state import CHECKPOINT_TYPES, ResearchState

ResearchGraph = CompiledStateGraph[ResearchState, AgentDeps, ResearchState, ResearchState]


def checkpoint_serde() -> JsonPlusSerializer:
    """Only our own state types may be rebuilt from a checkpoint (plus LangGraph's safe set)."""
    return JsonPlusSerializer(allowed_msgpack_modules=list(CHECKPOINT_TYPES))


def build_graph(checkpointer: BaseCheckpointSaver[str] | None) -> ResearchGraph:
    builder = StateGraph(ResearchState, context_schema=AgentDeps)
    # supervisor and critic route with Command(goto=...); `destinations` documents the edges.
    builder.add_node("supervisor", nodes.supervisor, destinations=("filings", "market", END))
    builder.add_node("filings", nodes.filings)
    builder.add_node("market", nodes.market)
    builder.add_node("analyst", nodes.analyst)
    builder.add_node("critic", nodes.critic, destinations=("analyst", "human_review"))
    builder.add_node("human_review", nodes.human_review)

    builder.add_edge(START, "supervisor")
    builder.add_edge(["filings", "market"], "analyst")  # join: waits for both branches
    builder.add_edge("analyst", "critic")
    builder.add_edge("human_review", END)
    return builder.compile(checkpointer=checkpointer)
