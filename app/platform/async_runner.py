from __future__ import annotations

import atexit
import asyncio
from functools import lru_cache
from typing import Awaitable, TypeVar

T = TypeVar("T")


@lru_cache(maxsize=1)
def get_async_runner() -> asyncio.Runner:
    runner = asyncio.Runner()
    atexit.register(runner.close)
    return runner


def run_async(coro: Awaitable[T]) -> T:
    return get_async_runner().run(coro)
