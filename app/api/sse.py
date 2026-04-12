"""SSE utilities for streaming teacher session completion."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

logger = logging.getLogger(__name__)


def sse_event(event: str, data: Any) -> str:
    """Format a single SSE event."""
    payload = json.dumps(data) if not isinstance(data, str) else data
    return f"event: {event}\ndata: {payload}\n\n"


async def stream_session_completion(
    turn_id: str,
    repo: Any,
    redis_cache: Any,
    *,
    wait_seconds: float = 180.0,
    poll_interval: float = 2.0,
    pre_pubsub: Any = None,
    pre_channel: str | None = None,
) -> AsyncGenerator[str, None]:
    """Yield SSE events for turn completion via DB polling.

    Uses pre-subscribed Redis pub/sub for fast notification when available,
    falls back to DB polling every poll_interval seconds.
    """
    yield sse_event("accepted", {"turn_id": turn_id})

    deadline = time.monotonic() + max(0.0, wait_seconds)
    last_state: str | None = None

    while time.monotonic() <= deadline:
        turn = await repo.get_chat_turn(turn_id)
        if turn is not None:
            state = turn["state"]
            if state == "completed" and turn.get("final_result_json"):
                yield sse_event("result", turn["final_result_json"])
                return
            if state == "failed":
                yield sse_event("error", {"detail": turn.get("error_message") or "Teacher session failed."})
                return
            if state != last_state:
                last_state = state
                yield sse_event("progress", {"state": state})

        # Wait for Redis notification or poll interval
        if pre_pubsub is not None:
            try:
                msg = await asyncio.wait_for(
                    pre_pubsub.get_message(ignore_subscribe_messages=True, timeout=poll_interval),
                    timeout=poll_interval + 1.0,
                )
                if msg is not None and msg.get("type") == "message":
                    continue  # Got notification, check DB immediately
            except (asyncio.TimeoutError, Exception):
                pass
        else:
            await asyncio.sleep(poll_interval)

    yield sse_event("error", {"detail": "Teacher session timed out."})
