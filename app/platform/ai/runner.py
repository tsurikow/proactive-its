from __future__ import annotations

from typing import Any, TypeVar

from pydantic_ai import Agent

from app.platform.ai.openrouter import build_model
from app.platform.config import Settings


T_Output = TypeVar("T_Output")


async def run_native_agent(
    agent: Agent[Any, T_Output],
    *,
    settings: Settings | None,
    prompt: str,
    model_name: str | None,
    temperature: float | None,
    timeout_seconds: float | None,
    extra_body: dict[str, Any] | None = None,
) -> T_Output:
    llm_model = build_model(
        settings,
        model=model_name,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        extra_body=extra_body,
    )
    if llm_model is None:
        raise RuntimeError("llm_unavailable")
    return (await agent.run(prompt, model=llm_model)).output


__all__ = ["run_native_agent"]
