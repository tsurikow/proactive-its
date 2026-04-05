from __future__ import annotations

from typing import Any

from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.platform.config import Settings, get_settings


def llm_available(settings: Settings | None = None) -> bool:
    resolved = settings or get_settings()
    return bool(resolved.openrouter_api_key and resolved.openrouter_model)


def build_model(
    settings: Settings | None = None,
    *,
    model: str | None = None,
    temperature: float | None = None,
    timeout_seconds: float | None = None,
    extra_body: dict[str, Any] | None = None,
) -> OpenRouterModel | None:
    resolved = settings or get_settings()
    model_name = model or resolved.openrouter_model
    if not resolved.openrouter_api_key or not model_name:
        return None
    model_settings: dict[str, Any] = {
        "timeout": timeout_seconds,
        "extra_body": extra_body,
    }
    normalized_temperature = _normalize_temperature(model_name, temperature)
    if normalized_temperature is not None:
        model_settings["temperature"] = normalized_temperature
    return OpenRouterModel(
        model_name,
        provider=OpenRouterProvider(api_key=resolved.openrouter_api_key),
        settings=model_settings,
    )


def _normalize_temperature(model_name: str, temperature: float | None) -> float | None:
    if temperature is None:
        return None
    normalized = str(model_name or "").lower()
    if "gpt-5" in normalized and "gpt-5-chat" not in normalized:
        return None
    return temperature


__all__ = ["build_model", "llm_available"]
