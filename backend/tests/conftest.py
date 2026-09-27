import asyncio
import sys
from collections.abc import Callable, Mapping

import pytest

if sys.platform == "win32":
    # psycopg (LangGraph's Postgres checkpointer) can't run on Windows' default Proactor loop.
    # Defined only on Windows: pytest-asyncio rejects an implementation that returns None.
    def pytest_asyncio_loop_factories(
        config: pytest.Config, item: pytest.Item
    ) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
        return {"selector": asyncio.SelectorEventLoop}
