from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from email_validator import EmailNotValidError, validate_email
import hmac
import logging

from fastapi import Request
from sqlalchemy.exc import IntegrityError

from app.auth.mailer import AuthMailer
from app.auth.rate_limit import AuthRateLimiter
from app.auth.repository import AuthRepository
from app.auth.security import (
    PASSWORD_HASHER,
    future_time,
    hash_secret,
    load_reset_payload,
    new_learner_id,
    new_opaque_token,
    sign_reset_payload,
    utc_now,
)
from app.platform.chat.models import Learner
from app.platform.config import Settings
from app.platform.logging import log_event


PASSWORD_RESET_KIND = "password_reset"
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AuthenticatedLearner:
    id: str
    first_name: str
    last_name: str
    email: str
    is_active: bool


@dataclass(slots=True)
class AuthServiceError(Exception):
    code: str
    message: str
    field_errors: dict[str, str] | None = None
    status_code: int = 400


@dataclass(slots=True)
class AuthSessionResult:
    learner: AuthenticatedLearner
    session_token: str


class AuthService:
    def __init__(
        self,
        repository: AuthRepository,
        mailer: AuthMailer,
        settings: Settings,
        rate_limiter: AuthRateLimiter | None = None,
    ) -> None:
        self.repository = repository
        self.mailer = mailer
        self.settings = settings
        self.rate_limiter = rate_limiter or AuthRateLimiter()

    def normalize_email(self, email: str) -> str:
        try:
            normalized = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise AuthServiceError(
                code="invalid_email",
                message="Enter a valid email address.",
                field_errors={"email": "Enter a valid email address."},
                status_code=422,
            ) from exc
        return normalized.lower()

    def validate_password(self, password: str) -> str:
        value = password.strip()
        if len(value) < 8:
            raise AuthServiceError(
                code="password_too_short",
                message="Password must be at least 8 characters.",
                field_errors={"password": "Password must be at least 8 characters."},
                status_code=422,
            )
        if len(value) > 128:
            raise AuthServiceError(
                code="password_too_long",
                message="Password must be 128 characters or fewer.",
                field_errors={"password": "Password must be 128 characters or fewer."},
                status_code=422,
            )
        return value

    def normalize_name(self, value: str, *, field_name: str, label: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise AuthServiceError(
                code=f"invalid_{field_name}",
                message=f"{label} is required.",
                field_errors={field_name: f"{label} is required."},
                status_code=422,
            )
        if len(normalized) > 80:
            raise AuthServiceError(
                code=f"invalid_{field_name}",
                message=f"{label} must be 80 characters or fewer.",
                field_errors={field_name: f"{label} must be 80 characters or fewer."},
                status_code=422,
            )
        return normalized

    async def signup(
        self,
        *,
        first_name: str,
        last_name: str,
        email: str,
        password: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> AuthSessionResult:
        normalized_first_name = self.normalize_name(first_name, field_name="first_name", label="First name")
        normalized_last_name = self.normalize_name(last_name, field_name="last_name", label="Last name")
        normalized_email = self.normalize_email(email)
        normalized_password = self.validate_password(password)
        limiter_key = ip_address or normalized_email
        self._check_rate_limit(
            "signup",
            limiter_key,
            limit=self.settings.auth_signup_attempt_limit,
            window_seconds=self.settings.auth_signup_attempt_window_seconds,
        )

        try:
            async with self.repository.session_scope() as session:
                await self._cleanup_auth_state(session=session)
                existing = await self.repository.get_learner_by_email(normalized_email, session=session)
                if existing is not None:
                    self._record_rate_limit_hit(
                        "signup",
                        limiter_key,
                        window_seconds=self.settings.auth_signup_attempt_window_seconds,
                    )
                    log_event(logger, "auth.signup_failed", email=normalized_email, reason="email_already_registered")
                    raise AuthServiceError(
                        code="email_already_registered",
                        message="An account with this email already exists.",
                        field_errors={"email": "An account with this email already exists."},
                        status_code=409,
                    )
                learner = await self.repository.create_learner(
                    learner_id=new_learner_id(),
                    first_name=normalized_first_name,
                    last_name=normalized_last_name,
                    email=normalized_email,
                    password_hash=PASSWORD_HASHER.hash(normalized_password),
                    session=session,
                )
                result = await self._create_session_for_learner(
                    learner,
                    user_agent=user_agent,
                    ip_address=ip_address,
                    session=session,
                )
        except IntegrityError as exc:  # pragma: no cover
            self._record_rate_limit_hit(
                "signup",
                limiter_key,
                window_seconds=self.settings.auth_signup_attempt_window_seconds,
            )
            raise AuthServiceError(
                code="email_already_registered",
                message="An account with this email already exists.",
                field_errors={"email": "An account with this email already exists."},
                status_code=409,
            ) from exc

        self._clear_rate_limit("signup", limiter_key)
        log_event(logger, "auth.signup_succeeded", learner_id=result.learner.id, email=result.learner.email)
        return result

    async def login(
        self,
        *,
        email: str,
        password: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> AuthSessionResult:
        normalized_email = self.normalize_email(email)
        limiter_key = (ip_address or "unknown", normalized_email)
        self._check_rate_limit(
            "login",
            limiter_key,
            limit=self.settings.auth_login_attempt_limit,
            window_seconds=self.settings.auth_login_attempt_window_seconds,
        )
        learner = await self.repository.get_learner_by_email(normalized_email)
        if learner is None or not learner.password_hash or not learner.is_active:
            self._record_rate_limit_hit(
                "login",
                limiter_key,
                window_seconds=self.settings.auth_login_attempt_window_seconds,
            )
            log_event(logger, "auth.login_failed", email=normalized_email, reason="invalid_credentials")
            raise AuthServiceError(
                code="invalid_credentials",
                message="Email or password is incorrect.",
                status_code=401,
            )
        if not PASSWORD_HASHER.verify(password, learner.password_hash):
            self._record_rate_limit_hit(
                "login",
                limiter_key,
                window_seconds=self.settings.auth_login_attempt_window_seconds,
            )
            log_event(logger, "auth.login_failed", email=normalized_email, reason="invalid_credentials")
            raise AuthServiceError(
                code="invalid_credentials",
                message="Email or password is incorrect.",
                status_code=401,
            )

        async with self.repository.session_scope() as session:
            await self._cleanup_auth_state(session=session)
            db_learner = await self.repository.get_learner_by_id(learner.id, session=session)
            if db_learner is None:
                self._record_rate_limit_hit(
                    "login",
                    limiter_key,
                    window_seconds=self.settings.auth_login_attempt_window_seconds,
                )
                raise AuthServiceError(
                    code="invalid_credentials",
                    message="Email or password is incorrect.",
                    status_code=401,
                )
            result = await self._create_session_for_learner(
                db_learner,
                user_agent=user_agent,
                ip_address=ip_address,
                session=session,
            )

        self._clear_rate_limit("login", limiter_key)
        log_event(logger, "auth.login_succeeded", learner_id=result.learner.id, email=result.learner.email)
        return result

    async def get_current_learner(self, session_token: str | None) -> AuthenticatedLearner | None:
        if not session_token:
            return None
        resolved = await self.repository.get_active_session(hash_secret(session_token), utc_now())
        if resolved is None:
            return None
        _session, learner = resolved
        if learner.email is None:
            return None
        return self._to_authenticated_learner(learner)

    async def logout(self, session_token: str | None) -> None:
        if not session_token:
            return
        await self.repository.revoke_session(hash_secret(session_token), utc_now())
        log_event(logger, "auth.logout_succeeded")

    async def issue_password_reset(self, *, email: str) -> None:
        if not self.settings.auth_reset_available:
            raise AuthServiceError(
                code="password_reset_unavailable",
                message="Password reset is not available right now.",
                status_code=503,
            )
        normalized_email = self.normalize_email(email)
        self._check_rate_limit(
            "password_reset",
            normalized_email,
            limit=self.settings.auth_reset_attempt_limit,
            window_seconds=self.settings.auth_reset_attempt_window_seconds,
        )
        learner = await self.repository.get_learner_by_email(normalized_email)
        if learner is None or not learner.email or not learner.is_active:
            self._record_rate_limit_hit(
                "password_reset",
                normalized_email,
                window_seconds=self.settings.auth_reset_attempt_window_seconds,
            )
            return
        raw_nonce = new_opaque_token()
        expires_at = future_time(minutes=self.settings.auth_reset_token_ttl_minutes)
        async with self.repository.session_scope() as session:
            await self._cleanup_auth_state(session=session)
            token_row = await self.repository.create_auth_token(
                learner_id=learner.id,
                token_kind=PASSWORD_RESET_KIND,
                token_hash=hash_secret(raw_nonce),
                expires_at=expires_at,
                session=session,
            )
        signed = sign_reset_payload(
            self.settings.auth_secret_key,
            {
                "token_id": token_row.id,
                "nonce": raw_nonce,
                "purpose": PASSWORD_RESET_KIND,
            },
        )
        reset_link = self.settings.frontend_public_url.rstrip("/") + "/?reset_token=" + signed
        await self.mailer.send_password_reset(email=learner.email, reset_link=reset_link)
        self._clear_rate_limit("password_reset", normalized_email)
        log_event(logger, "auth.password_reset_requested", learner_id=learner.id, email=learner.email)

    async def confirm_password_reset(self, *, token: str, new_password: str) -> None:
        if not self.settings.auth_reset_available:
            raise AuthServiceError(
                code="password_reset_unavailable",
                message="Password reset is not available right now.",
                status_code=503,
            )
        normalized_password = self.validate_password(new_password)
        payload = load_reset_payload(
            self.settings.auth_secret_key,
            token,
            max_age_seconds=int(timedelta(minutes=self.settings.auth_reset_token_ttl_minutes).total_seconds()),
        )
        if payload.get("purpose") != PASSWORD_RESET_KIND:
            raise AuthServiceError(code="invalid_reset_token", message="Reset link is invalid or expired.", status_code=422)
        token_id_raw = payload.get("token_id")
        nonce = payload.get("nonce")
        if not isinstance(token_id_raw, int) or not isinstance(nonce, str):
            raise AuthServiceError(code="invalid_reset_token", message="Reset link is invalid or expired.", status_code=422)

        async with self.repository.session_scope() as session:
            await self._cleanup_auth_state(session=session)
            token_row = await self.repository.get_active_auth_token(
                token_id=token_id_raw,
                token_kind=PASSWORD_RESET_KIND,
                now=utc_now(),
                session=session,
            )
            if token_row is None:
                raise AuthServiceError(code="invalid_reset_token", message="Reset link is invalid or expired.", status_code=422)
            if not hmac.compare_digest(token_row.token_hash, hash_secret(nonce)):
                raise AuthServiceError(code="invalid_reset_token", message="Reset link is invalid or expired.", status_code=422)
            learner = await self.repository.get_learner_by_id(token_row.learner_id, session=session)
            if learner is None or learner.email is None:
                raise AuthServiceError(code="invalid_reset_token", message="Reset link is invalid or expired.", status_code=422)
            now = utc_now()
            await self.repository.update_password(
                learner.id,
                PASSWORD_HASHER.hash(normalized_password),
                session=session,
            )
            await self.repository.mark_auth_token_used(token_row.id, now, session=session)
            await self.repository.revoke_all_sessions_for_learner(learner.id, now, session=session)
            await self._cleanup_auth_state(session=session)
        log_event(logger, "auth.password_reset_confirmed", learner_id=learner.id, email=learner.email)

    async def require_same_origin(self, request: Request) -> None:
        origin = request.headers.get("origin")
        if not origin:
            return
        allowed = {item.rstrip("/") for item in self.settings.cors_allow_origins}
        allowed.add(self.settings.frontend_public_url.rstrip("/"))
        if origin.rstrip("/") not in allowed:
            raise AuthServiceError(
                code="auth_origin_mismatch",
                message="Open the learner app through the main site origin to sign up or sign in.",
                status_code=403,
            )

    async def _cleanup_auth_state(self, *, session) -> None:
        now = utc_now()
        await self.repository.delete_expired_auth_tokens(now, session=session)
        await self.repository.delete_expired_auth_sessions(now, session=session)

    def _check_rate_limit(self, scope: str, key: object, *, limit: int, window_seconds: int) -> None:
        try:
            self.rate_limiter.check(scope, key, limit=limit, window_seconds=window_seconds)
        except ValueError as exc:
            raise AuthServiceError(
                code="rate_limited",
                message="Too many attempts. Please wait and try again.",
                status_code=429,
            ) from exc

    def _record_rate_limit_hit(self, scope: str, key: object, *, window_seconds: int) -> None:
        self.rate_limiter.hit(scope, key, window_seconds=window_seconds)

    def _clear_rate_limit(self, scope: str, key: object) -> None:
        self.rate_limiter.clear(scope, key)

    async def _create_session_for_learner(
        self,
        learner: Learner,
        *,
        user_agent: str | None,
        ip_address: str | None,
        session,
    ) -> AuthSessionResult:
        if learner.email is None:
            raise ValueError("learner_email_missing")
        raw_token = new_opaque_token()
        expires_at = future_time(hours=self.settings.auth_session_ttl_hours)
        now = utc_now()
        await self.repository.create_auth_session(
            learner_id=learner.id,
            token_hash=hash_secret(raw_token),
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
            session=session,
        )
        await self.repository.touch_last_login(learner.id, now, session=session)
        return AuthSessionResult(
            learner=self._to_authenticated_learner(learner),
            session_token=raw_token,
        )

    def _to_authenticated_learner(self, learner: Learner) -> AuthenticatedLearner:
        if learner.email is None:
            raise ValueError("learner_email_missing")
        return AuthenticatedLearner(
            id=learner.id,
            first_name=learner.first_name,
            last_name=learner.last_name,
            email=learner.email,
            is_active=bool(learner.is_active),
        )
