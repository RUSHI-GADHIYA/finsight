"""Operational metrics for the /metrics page: cost, latency, cache and guardrails, evals."""

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.observability.ops import Metrics, PostgresOpsLog

router = APIRouter(tags=["metrics"])
EVAL_RESULTS = Path(__file__).resolve().parents[2] / "evals" / "results"


def get_ops(request: Request) -> PostgresOpsLog:
    ops: PostgresOpsLog = request.app.state.ops
    return ops


class MetricsOut(Metrics):
    evals: dict[str, Any]  # latest committed eval results (evals/results/*.json)


def latest_evals(directory: Path = EVAL_RESULTS) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            results[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return results


@router.get("/metrics")
async def metrics(ops: Annotated[PostgresOpsLog, Depends(get_ops)]) -> MetricsOut:
    summary: BaseModel = await ops.metrics()
    return MetricsOut(**summary.model_dump(), evals=latest_evals())
