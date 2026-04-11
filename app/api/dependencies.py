from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, HTTPException, Request

from app.auth.mailer import AuthMailer
from app.auth.rate_limit import AuthRateLimiter, RedisRateLimiter
from app.platform.cache import RedisCache
from app.auth.repository import AuthRepository
from app.auth.service import AuthService, AuthenticatedLearner, AuthServiceError
from app.content.bootstrap import ContentBootstrapService
from app.platform.rag.answer_generator import AnswerGenerator
from app.platform.chat.repository import DurableChatRepository
from app.platform.chat.runtime import ChatService
from app.platform.config import get_settings
from app.platform.embeddings import AsyncEmbeddingClient
from app.platform.rag.grounded_answer_runtime import GroundedAnswerRuntime
from app.platform.chat.interaction_repository import InteractionRepository
from app.platform.rag.query_rewrite import QueryRewriteService
from app.platform.rag.retriever import DenseRetriever
from app.platform.vector_store import AsyncVectorStore
from app.state.services.learner_service import LearnerService
from app.state.repositories.learner_repository import LearnerStateRepository
from app.state.repositories.session_repository import SessionStateRepository
from app.state.services.service import TeacherStateService
from app.teacher.artifacts.artifacts import TeacherArtifactRuntime
from app.teacher.engine import TeacherEngine
from app.teacher.artifacts.lesson_generation import SectionLessonGenerator
from app.teacher.runtime import TeacherRuntime
from app.teacher.planning.pending_task_runtime import PendingTaskRuntime
from app.teacher.planning.section_understanding_service import SectionUnderstandingService
from app.teacher.repository import TeacherRepository
from app.platform.logging import bind_request_context


@lru_cache(maxsize=1)
def get_redis_cache() -> RedisCache:
    settings = get_settings()
    return RedisCache(url=settings.redis_url, default_ttl=settings.redis_cache_ttl_seconds)


@lru_cache(maxsize=1)
def get_async_vector_store() -> AsyncVectorStore:
    return AsyncVectorStore(get_settings())


@lru_cache(maxsize=1)
def get_auth_repository() -> AuthRepository:
    return AuthRepository()


@lru_cache(maxsize=1)
def get_auth_mailer() -> AuthMailer:
    return AuthMailer(get_settings())


@lru_cache(maxsize=1)
def get_auth_rate_limiter() -> AuthRateLimiter:
    settings = get_settings()
    return RedisRateLimiter(redis_url=settings.redis_url)


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    return AuthService(get_auth_repository(), get_auth_mailer(), get_settings(), get_auth_rate_limiter())


@lru_cache(maxsize=1)
def get_content_bootstrap_service() -> ContentBootstrapService:
    return ContentBootstrapService(get_settings())


@lru_cache(maxsize=1)
def get_runtime_embedding_client() -> AsyncEmbeddingClient:
    settings = get_settings()
    redis = get_redis_cache() if settings.embedding_cache_enabled else None
    return AsyncEmbeddingClient(settings, redis_cache=redis)


@lru_cache(maxsize=1)
def get_chat_repository() -> InteractionRepository:
    return InteractionRepository()


@lru_cache(maxsize=1)
def get_durable_chat_repository() -> DurableChatRepository:
    return DurableChatRepository()


@lru_cache(maxsize=1)
def get_teacher_repository() -> TeacherRepository:
    return TeacherRepository()


@lru_cache(maxsize=1)
def get_learner_state_repository() -> LearnerStateRepository:
    return LearnerStateRepository()


@lru_cache(maxsize=1)
def get_session_state_repository() -> SessionStateRepository:
    return SessionStateRepository()


@lru_cache(maxsize=1)
def get_learner_service() -> LearnerService:
    return LearnerService(get_learner_state_repository(), settings=get_settings())


@lru_cache(maxsize=1)
def get_grounded_answer_runtime() -> GroundedAnswerRuntime:
    settings = get_settings()
    return GroundedAnswerRuntime(
        settings,
        retriever=DenseRetriever(
            settings,
            embedder=get_runtime_embedding_client(),
            store=get_async_vector_store(),
        ),
        generator=AnswerGenerator(settings),
        rewriter=QueryRewriteService(settings),
    )


@lru_cache(maxsize=1)
def get_chat_service() -> ChatService:
    settings = get_settings()
    return ChatService(
        chat_repository=get_durable_chat_repository(),
        interaction_repository=get_chat_repository(),
        learner_state_repository=get_learner_state_repository(),
        grounded_answer_runtime=get_grounded_answer_runtime(),
        redis_cache=get_redis_cache(),
        settings=settings,
    )


@lru_cache(maxsize=1)
def get_section_understanding_service() -> SectionUnderstandingService:
    return SectionUnderstandingService(
        repository=get_teacher_repository(),
        vector_store=get_async_vector_store(),
        engine=get_teacher_engine(),
    )


@lru_cache(maxsize=1)
def get_teacher_artifact_runtime() -> TeacherArtifactRuntime:
    settings = get_settings()
    return TeacherArtifactRuntime(
        repository=get_teacher_repository(),
        learner_service=get_learner_service(),
        vector_store=get_async_vector_store(),
        lesson_generator=SectionLessonGenerator(settings),
        settings=settings,
    )


@lru_cache(maxsize=1)
def get_teacher_state_service() -> TeacherStateService:
    settings = get_settings()
    return TeacherStateService(
        learner_repository=get_learner_state_repository(),
        session_repository=get_session_state_repository(),
        learner_service=get_learner_service(),
        book_json_path=settings.book_json_path,
        settings=settings,
    )



@lru_cache(maxsize=1)
def get_pending_task_runtime() -> PendingTaskRuntime:
    return PendingTaskRuntime(
        session_repository=get_session_state_repository(),
    )


@lru_cache(maxsize=1)
def get_teacher_engine() -> TeacherEngine:
    settings = get_settings()
    return TeacherEngine(
        model=settings.openrouter_model,
        answer_check_model=settings.teacher_answer_check_model or settings.teacher_reasoning_model,
        section_understanding_model=settings.teacher_section_understanding_model or settings.teacher_reasoning_model,
        settings=settings,
    )


@lru_cache(maxsize=1)
def get_teacher_runtime() -> TeacherRuntime:
    settings = get_settings()
    return TeacherRuntime(
        engine=get_teacher_engine(),
        state_service=get_teacher_state_service(),
        session_repository=get_session_state_repository(),
        learner_service=get_learner_service(),
        section_understanding_service=get_section_understanding_service(),
        pending_task_runtime=get_pending_task_runtime(),
        artifact_runtime=get_teacher_artifact_runtime(),
        settings=settings,
    )


async def get_current_authenticated_learner(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthenticatedLearner | None:
    session_token = request.cookies.get(get_settings().auth_cookie_name)
    return await auth_service.get_current_learner(session_token)


async def require_authenticated_learner(
    request: Request,
    learner: AuthenticatedLearner | None = Depends(get_current_authenticated_learner),
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthenticatedLearner:
    try:
        await auth_service.require_same_origin(request)
    except AuthServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    if learner is None:
        raise HTTPException(status_code=401, detail="authentication_required")
    bind_request_context(learner_id=learner.id)
    return learner
