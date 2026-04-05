from __future__ import annotations

import contextvars
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any


_request_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "request_context", default={}
)


def _normalize_field(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _normalize_field(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_field(item) for item in value]
    return str(value)


def bind_request_context(**fields: Any) -> contextvars.Token[dict[str, Any]]:
    payload = dict(_request_context.get({}))
    payload.update({key: _normalize_field(value) for key, value in fields.items() if value is not None})
    return _request_context.set(payload)


def reset_request_context(token: contextvars.Token[dict[str, Any]]) -> None:
    _request_context.reset(token)


def get_request_context() -> dict[str, Any]:
    return dict(_request_context.get({}))


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = get_request_context()
        for key, value in context.items():
            setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service_name: str, environment: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event_name", None) or "log",
            "service": self.service_name,
            "environment": self.environment,
        }
        message = record.getMessage()
        if message and message != payload["event"]:
            payload["message"] = message

        for field in ("request_id", "method", "path", "learner_id"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = _normalize_field(value)

        event_fields = getattr(record, "event_fields", None)
        if isinstance(event_fields, dict):
            payload.update({key: _normalize_field(value) for key, value in event_fields.items()})

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True)


class PlainFormatter(logging.Formatter):
    def __init__(self, *, service_name: str, environment: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{datetime.fromtimestamp(record.created, UTC).isoformat()} | "
            f"{record.levelname} | {record.name} | "
            f"{getattr(record, 'event_name', None) or record.getMessage()} | "
            f"{self.service_name} | {self.environment}"
        )

        context = {
            field: getattr(record, field, None)
            for field in ("request_id", "method", "path", "learner_id")
            if getattr(record, field, None) is not None
        }
        event_fields = getattr(record, "event_fields", None)
        if isinstance(event_fields, dict):
            context.update(event_fields)
        if context:
            base = f"{base} | {json.dumps(_normalize_field(context), ensure_ascii=True, sort_keys=True)}"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


def configure_logging() -> None:
    from app.platform.config import get_settings

    settings = get_settings()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(str(settings.log_level).upper())

    handler = logging.StreamHandler()
    handler.addFilter(RequestContextFilter())
    if settings.log_format == "plain":
        handler.setFormatter(PlainFormatter(service_name=settings.service_name, environment=settings.app_env))
    else:
        handler.setFormatter(JsonFormatter(service_name=settings.service_name, environment=settings.app_env))
    root.addHandler(handler)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    logger.info(
        event,
        extra={
            "event_name": event,
            "event_fields": {key: _normalize_field(value) for key, value in fields.items()},
        },
    )
