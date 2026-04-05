from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from itsdangerous import BadSignature, BadTimeSignature, URLSafeTimedSerializer
from pwdlib import PasswordHash


PASSWORD_HASHER = PasswordHash.recommended()


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def new_learner_id() -> str:
    return f"learner_{secrets.token_hex(16)}"


def utc_now() -> datetime:
    return datetime.now(UTC)


def future_time(*, hours: int = 0, minutes: int = 0) -> datetime:
    return utc_now() + timedelta(hours=hours, minutes=minutes)


def make_reset_serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key=secret_key, salt="learner-password-reset")


def sign_reset_payload(secret_key: str, payload: dict[str, str | int]) -> str:
    return make_reset_serializer(secret_key).dumps(payload)


def load_reset_payload(secret_key: str, token: str, *, max_age_seconds: int) -> dict[str, str | int]:
    serializer = make_reset_serializer(secret_key)
    try:
        payload = serializer.loads(token, max_age=max_age_seconds)
    except (BadSignature, BadTimeSignature) as exc:
        raise ValueError("invalid_reset_token") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_reset_token")
    return payload
