from __future__ import annotations

from typing import Any

from app.platform.config import Settings

try:
    import logfire
except ImportError:  # pragma: no cover - dependency optional until installed
    logfire = None


_configured = False


def configure_observability(settings: Settings) -> None:
    global _configured
    if _configured or not settings.logfire_enabled or logfire is None:
        return
    kwargs: dict[str, Any] = {
        "service_name": settings.logfire_service_name,
        "environment": settings.logfire_environment,
        "send_to_logfire": bool(settings.logfire_token),
    }
    if settings.logfire_token:
        kwargs["token"] = settings.logfire_token
    logfire.configure(**kwargs)
    logfire.instrument_pydantic_ai()
    _configured = True


def instrument_fastapi_app(app: Any, settings: Settings) -> None:
    if not settings.logfire_enabled or logfire is None:
        return
    logfire.instrument_fastapi(app)


__all__ = ["configure_observability", "instrument_fastapi_app"]
