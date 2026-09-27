"""Single place to obtain the OpenAI client; model names come from settings.

Smoke test:  uv run python -m app.llm
"""

import asyncio
from functools import lru_cache

from openai import AsyncOpenAI

from app.config import get_settings


@lru_cache
def get_openai_client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set (see .env.example)")
    return AsyncOpenAI(api_key=settings.openai_api_key, max_retries=3)


async def _smoke_test() -> None:
    settings = get_settings()
    client = get_openai_client()
    available = {m.id async for m in client.models.list()}
    for name in (settings.llm_fast_model, settings.llm_smart_model):
        print(f"{name}: {'available' if name in available else 'NOT AVAILABLE on this key'}")

    resp = await client.responses.create(
        model=settings.llm_fast_model, input="Reply with exactly: FinSight OK"
    )
    print(f"{settings.llm_fast_model} replied: {resp.output_text!r}")


if __name__ == "__main__":
    asyncio.run(_smoke_test())
