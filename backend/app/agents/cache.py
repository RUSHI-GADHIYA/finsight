"""Semantic cache of human-approved reports (Redis vector search, local bge embeddings).

Only signed-off reports are cached: a hit returns an answer a reviewer already verified, at
zero LLM cost. The cache is optional; if Redis is down, lookups are misses.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

import numpy as np
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.commands.search.field import NumericField, TextField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from redis.exceptions import ResponseError

log = logging.getLogger(__name__)

INDEX = "finsight:cache:idx"
PREFIX = "finsight:cache:"


class CacheHit(BaseModel):
    report_id: int
    question: str
    similarity: float
    cached_at: datetime


class SemanticCache(Protocol):
    async def lookup(self, question: str) -> CacheHit | None: ...

    async def add(self, question: str, report_id: int) -> None: ...


class RedisSemanticCache:
    def __init__(
        self,
        redis: Redis,
        embed: Callable[[str], list[float]],
        *,
        dim: int,
        threshold: float,
        ttl_days: int,
        index: str = INDEX,
        prefix: str = PREFIX,
    ) -> None:
        self._redis = redis
        self._index = index
        self._prefix = prefix
        self._embed = embed
        self._dim = dim
        self.threshold = threshold
        self._ttl = ttl_days * 86_400
        self._ready = False

    async def _vector(self, question: str) -> bytes:
        vec = await asyncio.to_thread(self._embed, question)
        return np.asarray(vec, dtype=np.float32).tobytes()

    async def _ensure_index(self) -> None:
        if self._ready:
            return
        try:
            await self._redis.ft(self._index).info()  # type: ignore[no-untyped-call]
        except ResponseError:
            await self._redis.ft(self._index).create_index(
                [
                    TextField("question"),
                    NumericField("report_id"),
                    NumericField("cached_at"),
                    VectorField(
                        "embedding",
                        "HNSW",
                        {"TYPE": "FLOAT32", "DIM": self._dim, "DISTANCE_METRIC": "COSINE"},
                    ),
                ],
                definition=IndexDefinition(prefix=[self._prefix], index_type=IndexType.HASH),  # type: ignore[no-untyped-call]
            )
        self._ready = True

    async def lookup(self, question: str) -> CacheHit | None:
        try:
            await self._ensure_index()
            query = (
                Query("*=>[KNN 1 @embedding $vec AS distance]")
                .return_fields("question", "report_id", "cached_at", "distance")
                .sort_by("distance")
                .dialect(2)
            )
            result = await self._redis.ft(self._index).search(
                query, query_params={"vec": await self._vector(question)}
            )
        except Exception:
            log.warning("semantic cache lookup failed; treating as a miss", exc_info=True)
            return None
        if not result.docs:
            return None
        doc = result.docs[0]
        similarity = 1.0 - float(doc.distance)
        if similarity < self.threshold:
            return None
        return CacheHit(
            report_id=int(doc.report_id),
            question=doc.question,
            similarity=round(similarity, 4),
            cached_at=datetime.fromtimestamp(float(doc.cached_at), UTC),
        )

    async def add(self, question: str, report_id: int) -> None:
        try:
            await self._ensure_index()
            key = f"{self._prefix}{report_id}"
            await self._redis.hset(
                key,
                mapping={
                    "question": question,
                    "report_id": report_id,
                    "cached_at": time.time(),
                    "embedding": await self._vector(question),
                },
            )
            await self._redis.expire(key, self._ttl)
        except Exception:
            log.warning("semantic cache write failed", exc_info=True)


async def _backfill() -> None:
    """Load approved reports from the last `cache_ttl_days` into the cache (e.g. after a
    Redis reset). Free: embeddings are local."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import SavedReport
    from app.db.session import get_sessionmaker
    from app.rag.embed import get_embedder

    settings = get_settings()
    since = datetime.now(UTC) - timedelta(days=settings.cache_ttl_days)
    async with get_sessionmaker()() as session:
        rows = list(
            await session.execute(
                select(SavedReport.id, SavedReport.question).where(SavedReport.created_at >= since)
            )
        )
    redis = Redis.from_url(settings.redis_url)
    cache = RedisSemanticCache(
        redis,
        get_embedder().embed_query,
        dim=settings.embedding_dim,
        threshold=settings.cache_similarity,
        ttl_days=settings.cache_ttl_days,
    )
    for report_id, question in rows:
        await cache.add(question, report_id)
    await redis.aclose()
    print(f"Cached {len(rows)} approved reports from the last {settings.cache_ttl_days} days")


if __name__ == "__main__":  # uv run python -m app.agents.cache
    asyncio.run(_backfill())
