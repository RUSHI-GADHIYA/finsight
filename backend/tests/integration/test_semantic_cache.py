"""The Redis semantic cache against a real Redis 8 (vector search). Uses its own index and
key prefix, removed afterwards. Skipped when Redis isn't reachable."""

import hashlib
import math
import re
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from app.agents.cache import RedisSemanticCache
from app.config import get_settings

pytestmark = pytest.mark.integration
DIM = 384


def embed(text: str) -> list[float]:
    """Hashed bag of words: questions sharing words get similar vectors."""
    vec = [0.0] * DIM
    for word in re.findall(r"[a-z]+", text.lower()):
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


@pytest.fixture
async def cache() -> AsyncIterator[RedisSemanticCache]:
    redis = Redis.from_url(get_settings().redis_url)
    try:
        await redis.ping()
    except (OSError, ConnectionError) as exc:
        await redis.aclose()
        pytest.skip(f"Redis not reachable: {exc}")
    tag = uuid.uuid4().hex[:8]
    index, prefix = f"finsight:test:{tag}:idx", f"finsight:test:{tag}:"
    yield RedisSemanticCache(
        redis, embed, dim=DIM, threshold=0.9, ttl_days=1, index=index, prefix=prefix
    )
    await redis.ft(index).dropindex(delete_documents=True)
    await redis.aclose()


async def test_finds_a_near_duplicate_question_and_ignores_different_ones(
    cache: RedisSemanticCache,
) -> None:
    assert await cache.lookup("What are NVIDIA's export control risks?") is None  # empty

    await cache.add("What are NVIDIA's export control risks?", report_id=7)

    hit = await cache.lookup("what are nvidia's export control risks")
    assert hit is not None
    assert hit.report_id == 7
    assert hit.similarity > 0.99
    assert await cache.lookup("How does Tesla describe competition in China?") is None
