from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import LearnerAuthSession, LearnerAuthToken
from app.platform.chat.models import Learner
from app.platform.db import get_session


class AuthRepository:
    @asynccontextmanager
    async def session_scope(self) -> AsyncIterator[AsyncSession]:
        async with get_session() as session:
            yield session

    async def get_learner_by_email(self, email: str, *, session: AsyncSession | None = None) -> Learner | None:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.get_learner_by_email(email, session=owned_session)
        return await session.scalar(select(Learner).where(Learner.email == email).limit(1))

    async def get_learner_by_id(self, learner_id: str, *, session: AsyncSession | None = None) -> Learner | None:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.get_learner_by_id(learner_id, session=owned_session)
        return await session.scalar(select(Learner).where(Learner.id == learner_id).limit(1))

    async def create_learner(
        self,
        *,
        learner_id: str,
        first_name: str,
        last_name: str,
        email: str,
        password_hash: str,
        session: AsyncSession | None = None,
    ) -> Learner:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.create_learner(
                    learner_id=learner_id,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    password_hash=password_hash,
                    session=owned_session,
                )
        learner = Learner(
            id=learner_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            password_hash=password_hash,
            is_active=True,
        )
        session.add(learner)
        await session.flush()
        await session.refresh(learner)
        return learner

    async def touch_last_login(self, learner_id: str, logged_at: datetime, *, session: AsyncSession | None = None) -> None:
        if session is None:
            async with self.session_scope() as owned_session:
                await self.touch_last_login(learner_id, logged_at, session=owned_session)
                return
        await session.execute(
            update(Learner)
            .where(Learner.id == learner_id)
            .values(last_login_at=logged_at, updated_at=logged_at)
        )

    async def update_password(self, learner_id: str, password_hash: str, *, session: AsyncSession | None = None) -> None:
        if session is None:
            async with self.session_scope() as owned_session:
                await self.update_password(learner_id, password_hash, session=owned_session)
                return
        now = datetime.now(UTC)
        await session.execute(
            update(Learner)
            .where(Learner.id == learner_id)
            .values(password_hash=password_hash, updated_at=now)
        )

    async def create_auth_session(
        self,
        *,
        learner_id: str,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None,
        ip_address: str | None,
        session: AsyncSession | None = None,
    ) -> LearnerAuthSession:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.create_auth_session(
                    learner_id=learner_id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                    user_agent=user_agent,
                    ip_address=ip_address,
                    session=owned_session,
                )
        auth_session = LearnerAuthSession(
            learner_id=learner_id,
            session_token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        session.add(auth_session)
        await session.flush()
        await session.refresh(auth_session)
        return auth_session

    async def get_active_session(
        self,
        token_hash: str,
        now: datetime,
        *,
        session: AsyncSession | None = None,
    ) -> tuple[LearnerAuthSession, Learner] | None:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.get_active_session(token_hash, now, session=owned_session)
        row = await session.execute(
            select(LearnerAuthSession, Learner)
            .join(Learner, Learner.id == LearnerAuthSession.learner_id)
            .where(
                LearnerAuthSession.session_token_hash == token_hash,
                LearnerAuthSession.revoked_at.is_(None),
                LearnerAuthSession.expires_at > now,
                Learner.is_active.is_(True),
            )
            .limit(1)
        )
        result = row.first()
        return None if result is None else (result[0], result[1])

    async def revoke_session(self, token_hash: str, revoked_at: datetime, *, session: AsyncSession | None = None) -> None:
        if session is None:
            async with self.session_scope() as owned_session:
                await self.revoke_session(token_hash, revoked_at, session=owned_session)
                return
        await session.execute(
            update(LearnerAuthSession)
            .where(
                LearnerAuthSession.session_token_hash == token_hash,
                LearnerAuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )

    async def revoke_all_sessions_for_learner(
        self,
        learner_id: str,
        revoked_at: datetime,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        if session is None:
            async with self.session_scope() as owned_session:
                await self.revoke_all_sessions_for_learner(learner_id, revoked_at, session=owned_session)
                return
        await session.execute(
            update(LearnerAuthSession)
            .where(
                LearnerAuthSession.learner_id == learner_id,
                LearnerAuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )

    async def create_auth_token(
        self,
        *,
        learner_id: str,
        token_kind: str,
        token_hash: str,
        expires_at: datetime,
        session: AsyncSession | None = None,
    ) -> LearnerAuthToken:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.create_auth_token(
                    learner_id=learner_id,
                    token_kind=token_kind,
                    token_hash=token_hash,
                    expires_at=expires_at,
                    session=owned_session,
                )
        token = LearnerAuthToken(
            learner_id=learner_id,
            token_kind=token_kind,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        session.add(token)
        await session.flush()
        await session.refresh(token)
        return token

    async def get_active_auth_token(
        self,
        *,
        token_id: int,
        token_kind: str,
        now: datetime,
        session: AsyncSession | None = None,
    ) -> LearnerAuthToken | None:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.get_active_auth_token(
                    token_id=token_id,
                    token_kind=token_kind,
                    now=now,
                    session=owned_session,
                )
        return await session.scalar(
            select(LearnerAuthToken).where(
                LearnerAuthToken.id == token_id,
                LearnerAuthToken.token_kind == token_kind,
                LearnerAuthToken.used_at.is_(None),
                LearnerAuthToken.expires_at > now,
            )
        )

    async def mark_auth_token_used(self, token_id: int, used_at: datetime, *, session: AsyncSession | None = None) -> None:
        if session is None:
            async with self.session_scope() as owned_session:
                await self.mark_auth_token_used(token_id, used_at, session=owned_session)
                return
        await session.execute(
            update(LearnerAuthToken)
            .where(LearnerAuthToken.id == token_id)
            .values(used_at=used_at)
        )

    async def delete_expired_auth_tokens(self, now: datetime, *, session: AsyncSession | None = None) -> None:
        if session is None:
            async with self.session_scope() as owned_session:
                await self.delete_expired_auth_tokens(now, session=owned_session)
                return
        await session.execute(
            delete(LearnerAuthToken).where(
                LearnerAuthToken.expires_at <= now,
            )
        )

    async def delete_expired_auth_sessions(self, now: datetime, *, session: AsyncSession | None = None) -> None:
        if session is None:
            async with self.session_scope() as owned_session:
                await self.delete_expired_auth_sessions(now, session=owned_session)
                return
        await session.execute(
            delete(LearnerAuthSession).where(
                LearnerAuthSession.expires_at <= now,
            )
        )
